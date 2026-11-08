import os
import re
import json
import logging
import asyncio
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

from cryptography.fernet import Fernet
from playwright.async_api import async_playwright

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logger = logging.getLogger(__name__)

USERNAME, OTP = range(2)

BASE_URL = "https://selectionway.com"
LOGIN_URL = BASE_URL + "/login"

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
SESSION_DIR = DATA_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

KEY = os.getenv("SESSION_ENCRYPTION_KEY")

BROWSERS = {}
USER_SESSIONS = {}


def encrypt_session(data):
    if not KEY:
        return None
    try:
        return Fernet(KEY.encode()).encrypt(json.dumps(data).encode())
    except Exception as e:
        logger.error(f"[Auth] Encryption error: {e}")
        return None


def decrypt_session(path):
    if not KEY or not path.exists():
        return None
    try:
        encrypted = path.read_bytes()
        return json.loads(Fernet(KEY.encode()).decrypt(encrypted))
    except Exception as e:
        logger.error(f"[Auth] Decryption error: {e}")
        return None


def session_path(user_id):
    return SESSION_DIR / f"{user_id}.enc"


async def inspect_page_structure(page, stage_name: str):
    logger.info(f"====== [DIAGNOSTIC: {stage_name}] ======")
    logger.info(f"Page URL: {page.url}")

    try:
        inputs = await page.query_selector_all("input")
        logger.info(f"Found {len(inputs)} input tags:")
        for idx, inp in enumerate(inputs):
            i_type = await inp.get_attribute("type")
            i_name = await inp.get_attribute("name")
            i_id = await inp.get_attribute("id")
            i_ph = await inp.get_attribute("placeholder")
            i_class = await inp.get_attribute("class")
            is_vis = await inp.is_visible()
            logger.info(
                f"  Input #{idx+1} -> type='{i_type}', name='{i_name}', id='{i_id}', placeholder='{i_ph}', visible={is_vis}, class='{i_class}'"
            )

        buttons = await page.query_selector_all("button, [role='button'], input[type='submit']")
        logger.info(f"Found {len(buttons)} button tags:")
        for idx, btn in enumerate(buttons):
            text = (await btn.inner_text()).strip() if await btn.is_visible() else "(hidden)"
            b_type = await btn.get_attribute("type")
            b_class = await btn.get_attribute("class")
            is_vis = await btn.is_visible()
            logger.info(
                f"  Button #{idx+1} -> text='{text}', type='{b_type}', visible={is_vis}, class='{b_class}'"
            )
    except Exception as e:
        logger.warning(f"Diagnostic error: {e}")

    logger.info(f"====== [END DIAGNOSTIC: {stage_name}] ======\n")


async def start_browser(user_id):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    )

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    saved = decrypt_session(session_path(user_id))

    if saved:
        try:
            if "cookies" in saved:
                await context.add_cookies(saved.get("cookies", []))
            for origin, values in saved.get("origins", {}).items():
                p = await context.new_page()
                await p.goto(origin, wait_until="commit")
                await p.evaluate(
                    """items => {
                        for (const [key, value] of Object.entries(items)) {
                            localStorage.setItem(key, value);
                        }
                    }""",
                    values,
                )
                await p.close()
            logger.info(f"[Auth] Restored session storage for {user_id}")
        except Exception:
            logger.exception("Session restore failed")

    page = await context.new_page()

    BROWSERS[user_id] = {
        "playwright": playwright,
        "browser": browser,
        "context": context,
        "page": page,
    }

    return page


async def save_session(user_id):
    item = BROWSERS.get(user_id)
    if not item:
        return

    try:
        state = await item["context"].storage_state()
        encrypted = encrypt_session(state)
        if encrypted:
            session_path(user_id).write_bytes(encrypted)
            logger.info(f"[Auth] Saved session for user {user_id}")

        USER_SESSIONS[user_id] = True
    except Exception:
        logger.exception("Could not save session")


async def close_browser(user_id):
    item = BROWSERS.pop(user_id, None)
    if item:
        try:
            await item["page"].close()
        except Exception:
            pass
        try:
            await item["browser"].close()
        except Exception:
            pass
        try:
            await item["playwright"].stop()
        except Exception:
            pass


async def find_login_input(page):
    selectors = [
        'input[type="tel"]',
        'input[type="email"]',
        'input[name*="phone" i]',
        'input[name*="mobile" i]',
        'input[name*="email" i]',
        'input[name*="username" i]',
        'input[placeholder*="phone" i]',
        'input[placeholder*="mobile" i]',
        'input[placeholder*="email" i]',
        'input[placeholder*="नंबर" i]',
        'input[type="text"]',
        'input:not([type="hidden"])',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible():
                logger.info(f"[Selector Match] Found username input: {selector}")
                return locator
        except Exception:
            continue

    return None


async def click_continue(page):
    names = [
        "Continue",
        "Next",
        "Login",
        "Continue with OTP",
        "Send OTP",
        "Get OTP",
        "Proceed",
        "आगे बढ़ें",
        "ओटीपी",
    ]

    for name in names:
        try:
            button = page.get_by_role("button", name=re.compile(name, re.I)).first
            if await button.is_visible():
                logger.info(f"[Selector Match] Clicked button via name: {name}")
                await button.click()
                return True
        except Exception:
            continue

    css_buttons = [
        "button[type='submit']",
        "form button",
        "button:has-text('OTP')",
        "button:has-text('Continue')",
    ]
    for css in css_buttons:
        try:
            btn = page.locator(css).first
            if await btn.is_visible():
                logger.info(f"[Selector Match] Clicked button via css: {css}")
                await btn.click()
                return True
        except Exception:
            continue

    return False


async def selectionway_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    saved = decrypt_session(session_path(user_id))

    if saved:
        await update.message.reply_text(
            "🎓 SelectionWay\n\n"
            "✅ आपका लॉगिन सेशन पहले से सुरक्षित सेव है!\n"
            "बैच देखने के लिए /swbatches टाइप करें, या नया लॉगिन करने के लिए अपना Phone/Email भेजें:"
        )
    else:
        await update.message.reply_text(
            "🎓 SelectionWay Login\n\n"
            "कृपया अपना रजिस्टर्ड Mobile Number या Email यहाँ भेजें।\n"
            "(रद्द करने के लिए /cancel टाइप करें)"
        )
    return USERNAME


async def receive_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.message.text.strip()

    if not username:
        await update.message.reply_text("कृपया वैध Phone या Email दर्ज करें।")
        return USERNAME

    context.user_data["sw_username"] = username
    status_msg = await update.message.reply_text(
        "⏳ SelectionWay लॉगिन पेज लोड हो रहा है..."
    )

    try:
        await close_browser(user_id)
        page = await start_browser(user_id)

        # सीधे https://selectionway.com/login खोलें
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        
        # इनपुट दिखने तक इंतज़ार करें
        try:
            await page.wait_for_selector("input", state="visible", timeout=15000)
        except Exception:
            pass

        await asyncio.sleep(2)
        await inspect_page_structure(page, stage_name="Login Page Loaded")

        field = await find_login_input(page)
        if not field:
            raise RuntimeError(f"Login input field नहीं मिला (URL: {page.url})")

        await field.fill(username)
        await asyncio.sleep(1)

        clicked = await click_continue(page)
        if not clicked:
            logger.info("[Auth] Pressing Enter key on input")
            await field.press("Enter")

        # OTP बॉक्स दिखने तक इंतज़ार करें
        await asyncio.sleep(3)
        await inspect_page_structure(page, stage_name="After Submit Username")

        await status_msg.edit_text(
            f"📩 `{username}` पर OTP भेजा गया है!\n\n"
            "कृपया प्राप्त OTP यहाँ भेजें:\n"
            "(रद्द करने के लिए /cancel)"
        )
        return OTP

    except Exception as e:
        logger.exception("Login start failed")
        await close_browser(user_id)
        await status_msg.edit_text(
            f"❌ लॉगिन शुरू करने में समस्या आई:\n`{str(e)}`\n\n"
            "कृपया दोबारा प्रयास करें।"
        )
        return ConversationHandler.END


async def receive_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    otp = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    if not re.fullmatch(r"\d{4,6}", otp):
        await update.effective_chat.send_message(
            "कृपया 4 या 6 अंकों का संख्यात्मक OTP भेजें।"
        )
        return OTP

    item = BROWSERS.get(user_id)
    if not item:
        await update.effective_chat.send_message(
            "⚠️ लॉगिन सत्र समाप्त हो गया। कृपया /selectionway से दोबारा शुरू करें।"
        )
        return ConversationHandler.END

    page = item["page"]
    status_msg = await update.effective_chat.send_message(
        "⏳ OTP सत्यापित किया जा रहा है..."
    )

    try:
        await inspect_page_structure(page, stage_name="Before OTP Fill")

        fields = page.locator(
            'input[autocomplete="one-time-code"], '
            'input[name*="otp" i], '
            'input[placeholder*="otp" i], '
            'input[inputmode="numeric"], '
            'input[type="text"]:not([readonly]), '
            'input[type="number"]'
        )

        count = await fields.count()
        logger.info(f"[Auth] Total potential OTP inputs found: {count}")

        if count >= len(otp):
            for i, digit in enumerate(otp):
                await fields.nth(i).fill(digit)
        elif count >= 1:
            await fields.first.fill(otp)
        else:
            logger.info(
                "[Auth] No explicit OTP input, typing directly via keyboard"
            )
            await page.keyboard.type(otp)

        await asyncio.sleep(1)

        verified = False
        for name in [
            "Verify OTP",
            "Verify",
            "Submit",
            "Login",
            "Confirm",
            "आगे बढ़ें",
        ]:
            try:
                button = page.get_by_role(
                    "button", name=re.compile(name, re.I)
                ).first
                if await button.is_visible():
                    await button.click()
                    verified = True
                    logger.info(f"[Auth] Clicked verify button: {name}")
                    break
            except Exception:
                continue

        if not verified:
            try:
                submit_btn = page.locator(
                    "button[type='submit'], button.btn-primary"
                ).first
                if await submit_btn.is_visible():
                    await submit_btn.click()
                    verified = True
            except Exception:
                pass

        if not verified:
            logger.info(
                "[Auth] Verify button not found by selector, pressing Enter"
            )
            await page.keyboard.press("Enter")

        await asyncio.sleep(4)
        await inspect_page_structure(page, stage_name="After OTP Verification")

        current_url = page.url.lower()
        if "/login" in current_url:
            raise RuntimeError(
                f"लॉगिन पूरा नहीं हो सका (Current URL: {page.url})"
            )

        await save_session(user_id)

        await status_msg.edit_text(
            "✅ **SelectionWay Login Successful!**\n\n"
            "आपका सेशन सुरक्षित सेव हो गया है। अब Batches चेक कर रहा हूँ..."
        )

        await show_batches(update, context)
        return ConversationHandler.END

    except Exception as e:
        logger.exception("OTP verification failed")
        await status_msg.edit_text(f"❌ OTP सत्यापन में समस्या आई:\n`{str(e)}`")
        await close_browser(user_id)
        return ConversationHandler.END


async def get_page_links(page):
    return await page.locator("a[href]").evaluate_all(
        """els => els.map(a => ({
            text: (a.innerText || a.textContent || '').trim(),
            href: a.href
        })).filter(x => x.href)"""
    )


async def show_batches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    item = BROWSERS.get(user_id)

    if not item:
        saved = decrypt_session(session_path(user_id))
        if saved:
            await start_browser(user_id)
            item = BROWSERS.get(user_id)

    if not item:
        await update.effective_chat.send_message(
            "कृपया पहले /selectionway से लॉगिन करें।"
        )
        return

    page = item["page"]

    try:
        await page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)

        links = await get_page_links(page)
        batches = []
        seen = set()

        for link in links:
            href = urljoin(BASE_URL, link["href"])
            if "/batches/" not in urlparse(href).path:
                continue
            if "/batches/class/" in href:
                continue
            if href in seen:
                continue

            seen.add(href)
            name = link["text"] or "Batch"
            batches.append({"name": name, "url": href})

        if not batches:
            await update.effective_chat.send_message(
                "⚠️ Batch links नहीं मिले। वेबसाइट डैशबोर्ड पर कोई सक्रिय बैच नहीं दिखा।"
            )
            return

        context.user_data["sw_batches"] = batches

        keyboard = []
        for i, batch in enumerate(batches[:30]):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        batch["name"][:50], callback_data=f"sw_batch:{i}"
                    )
                ]
            )

        await update.effective_chat.send_message(
            "📚 **उपलब्ध Batches:**\nजिस बैच का TXT डेटा निकालना है, उसे चुनें:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception:
        logger.exception("Batch listing failed")
        await update.effective_chat.send_message("❌ Batch list लोड नहीं हो पाई।")


async def export_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    item = BROWSERS.get(user_id)

    if not item:
        saved = decrypt_session(session_path(user_id))
        if saved:
            await start_browser(user_id)
            item = BROWSERS.get(user_id)

    if not item:
        await query.message.reply_text(
            "Session expire हो गया है। कृपया दोबारा /selectionway से लॉगिन करें।"
        )
        return

    try:
        index = int(query.data.split(":")[1])
        batches = context.user_data.get("sw_batches", [])

        if index >= len(batches):
            raise ValueError("Invalid batch selected")

        batch = batches[index]
        page = item["page"]

        await query.edit_message_text(
            f"⏳ **{batch['name']}** का डेटा निकाला जा रहा है...\nकृपया प्रतीक्षा करें।"
        )

        queue = [(batch["url"], 0)]
        visited = set()
        lectures = []
        subjects = []

        while queue and len(visited) < 100:
            url, depth = queue.pop(0)
            if url in visited:
                continue

            visited.add(url)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(1)

                links = await get_page_links(page)

                for link in links:
                    href = urljoin(BASE_URL, link["href"])
                    text = link["text"].strip()

                    if not text:
                        continue

                    if "/batches/class/" in href:
                        if href not in [x["url"] for x in lectures]:
                            lectures.append({"title": text, "url": href})

                    elif (
                        depth < 2
                        and urlparse(href).netloc == urlparse(BASE_URL).netloc
                        and "/batches/" in urlparse(href).path
                        and href not in visited
                        and href != batch["url"]
                    ):
                        if href not in [x[0] for x in queue]:
                            queue.append((href, depth + 1))
                            subjects.append(text)

            except Exception:
                logger.exception("Page scan failed: %s", url)

        lines = [
            "UNIVERSAL TXT GENERATOR",
            "=" * 45,
            f"Batch: {batch['name']}",
            f"Batch URL: {batch['url']}",
            "",
            "SUBJECTS / SECTIONS",
            "-" * 30,
        ]

        for subject in subjects:
            lines.append(subject)

        lines.extend(
            [
                "",
                "LECTURES",
                "-" * 30,
            ]
        )

        for number, lecture in enumerate(lectures, 1):
            lines.append(f"{number}. {lecture['title']}")
            lines.append(lecture["url"])
            lines.append("")

        lines.append(f"Total Lectures: {len(lectures)}")

        output = BytesIO("\n".join(lines).encode("utf-8"))
        output.name = f"{batch['name']}.txt"

        await query.message.reply_document(
            document=output,
            filename=f"{batch['name']}.txt",
            caption=f"✅ **{batch['name']}**\nकुल लेक्चर्स लिंक मिले: {len(lectures)}",
        )

    except Exception:
        logger.exception("Batch export failed")
        await query.message.reply_text("❌ Export में समस्या आई।")


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_SESSIONS.pop(user_id, None)
    context.user_data.clear()

    try:
        session_path(user_id).unlink(missing_ok=True)
    except Exception:
        pass

    await close_browser(user_id)
    await update.message.reply_text("SelectionWay सेशन लॉगआउट कर दिया गया है।")


async def cancel_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.clear()
    await close_brows
