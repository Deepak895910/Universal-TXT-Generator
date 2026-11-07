
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
LOGIN_URL = BASE_URL + "/login/?next=%2F"

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
SESSION_DIR = DATA_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

KEY = os.getenv("SESSION_ENCRYPTION_KEY")

BROWSERS = {}
USER_SESSIONS = {}


def encrypt_session(data):
    if not KEY:
        return None

    return Fernet(KEY.encode()).encrypt(
        json.dumps(data).encode()
    )


def decrypt_session(path):
    if not KEY or not path.exists():
        return None

    try:
        encrypted = path.read_bytes()
        return json.loads(
            Fernet(KEY.encode()).decrypt(encrypted)
        )
    except Exception:
        return None


def session_path(user_id):
    return SESSION_DIR / f"{user_id}.enc"


async def start_browser(user_id):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox"]
    )

    context = await browser.new_context()

    saved = decrypt_session(session_path(user_id))

    if saved:
        try:
            await context.add_cookies(saved.get("cookies", []))
            for origin, values in saved.get("origins", {}).items():
                page = await context.new_page()
                await page.goto(origin)
                await page.evaluate(
                    """items => {
                        for (const [key, value] of Object.entries(items)) {
                            localStorage.setItem(key, value);
                        }
                    }""",
                    values
                )
                await page.close()
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

        USER_SESSIONS[user_id] = True

    except Exception:
        logger.exception("Could not save session")


async def close_browser(user_id):
    item = BROWSERS.pop(user_id, None)

    if item:
        try:
            await item["browser"].close()
            await item["playwright"].stop()
        except Exception:
            pass


async def find_login_input(page):
    selectors = [
        'input[type="tel"]',
        'input[type="email"]',
        'input[name*="phone" i]',
        'input[name*="email" i]',
        'input[placeholder*="phone" i]',
        'input[placeholder*="email" i]',
        'input[type="text"]',
    ]

    for selector in selectors:
        locator = page.locator(selector).first

        try:
            if await locator.is_visible():
                return locator
        except Exception:
            continue

    return None


async def click_continue(page):
    names = ["Continue", "Next", "Login", "Continue with OTP"]

    for name in names:
        try:
            button = page.get_by_role(
                "button",
                name=re.compile(name, re.I)
            ).first

            if await button.is_visible():
                await button.click()
                return True
        except Exception:
            continue

    return False


async def selectionway_start(update, context):
    await update.message.reply_text(
        "🎓 SelectionWay Login\n\n"
        "Apna registered phone number ya email bhejiye.\n"
        "OTP Telegram par enter karna hoga."
    )
    return USERNAME


async def receive_username(update, context):
    user_id = update.effective_user.id
    username = update.message.text.strip()

    if not username:
        await update.message.reply_text("Phone ya email bhejiye.")
        return USERNAME

    context.user_data["sw_username"] = username

    try:
        await close_browser(user_id)
        page = await start_browser(user_id)

        await page.goto(
            LOGIN_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(2000)

        field = await find_login_input(page)

        if not field:
            raise RuntimeError("Login input not found")

        await field.fill(username)

        if not await click_continue(page):
            raise RuntimeError("Continue button not found")

        await page.wait_for_timeout(2500)

        await update.message.reply_text(
            "Agar SelectionWay ne OTP bheja hai, "
            "to wahi 4-digit OTP yahan bhejiye."
        )

        return OTP

    except Exception:
        logger.exception("Login start failed")
        await close_browser(user_id)

        await update.message.reply_text(
            "Login page par problem aayi.\n"
            "Website ke login form ka structure check karna hoga."
        )

        return ConversationHandler.END


async def receive_otp(update, context):
    user_id = update.effective_user.id
    otp = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    if not re.fullmatch(r"\d{4}", otp):
        await update.effective_chat.send_message(
            "4 digit OTP bhejiye."
        )
        return OTP

    item = BROWSERS.get(user_id)

    if not item:
        await update.effective_chat.send_message(
            "Login session nahi mili. /selectionway se dobara shuru karein."
        )
        return ConversationHandler.END

    page = item["page"]

    try:
        fields = page.locator(
            'input[autocomplete="one-time-code"], '
            'input[name*="otp" i], '
            'input[placeholder*="otp" i], '
            'input[inputmode="numeric"]'
        )

        count = await fields.count()

        if count >= 4:
            for i, digit in enumerate(otp):
                await fields.nth(i).fill(digit)
        elif count >= 1:
            await fields.first.fill(otp)
        else:
            raise RuntimeError("OTP field not found")

        verified = False

        for name in ["Verify OTP", "Verify", "Submit", "Continue"]:
            try:
                button = page.get_by_role(
                    "button",
                    name=re.compile(name, re.I)
                ).first

                if await button.is_visible():
                    await button.click()
                    verified = True
                    break
            except Exception:
                continue

        if not verified:
            raise RuntimeError("Verify button not found")

        await page.wait_for_timeout(5000)

        if "/login" in page.url.lower():
            raise RuntimeError("Login not confirmed")

        await save_session(user_id)

        await update.effective_chat.send_message(
            "✅ Login successful!\n"
            "Ab batches check kar raha hoon..."
        )

        await show_batches(update, context)

        return ConversationHandler.END

    except Exception:
        logger.exception("OTP verification failed")

        await update.effective_chat.send_message(
            "OTP verify nahi hua. Website ke OTP form ko check karna hoga."
        )

        return ConversationHandler.END


async def get_page_links(page):
    return await page.locator("a[href]").evaluate_all(
        """els => els.map(a => ({
            text: (a.innerText || a.textContent || '').trim(),
            href: a.href
        })).filter(x => x.href)"""
    )


async def show_batches(update, context):
    user_id = update.effective_user.id
    item = BROWSERS.get(user_id)

    if not item:
        await update.effective_chat.send_message(
            "Pehle /selectionway se login karein."
        )
        return

    page = item["page"]

    try:
        await page.goto(
            BASE_URL + "/",
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(3000)

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

            batches.append({
                "name": name,
                "url": href
            })

        if not batches:
            await update.effective_chat.send_message(
                "Batch links nahi mile. Website ke dashboard structure "
                "ko inspect karna hoga."
            )
            return

        context.user_data["sw_batches"] = batches

        keyboard = []

        for i, batch in enumerate(batches[:30]):
            keyboard.append([
                InlineKeyboardButton(
                    batch["name"][:60],
                    callback_data=f"sw_batch:{i}"
                )
            ])

        await update.effective_chat.send_message(
            "📚 Available Batch:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception:
        logger.exception("Batch listing failed")
        await update.effective_chat.send_message(
            "Batch list load nahi hui."
        )


async def export_batch(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    item = BROWSERS.get(user_id)

    if not item:
        await query.message.reply_text(
            "Session expired. Dobara login karein."
        )
        return

    try:
        index = int(query.data.split(":")[1])
        batches = context.user_data.get("sw_batches", [])

        if index >= len(batches):
            raise ValueError("Invalid batch")

        batch = batches[index]
        page = item["page"]

        await query.edit_message_text(
            "⏳ Batch ke lecture links collect ho rahe hain..."
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
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=45000
                )
                await page.wait_for_timeout(1200)

                links = await get_page_links(page)

                for link in links:
                    href = urljoin(BASE_URL, link["href"])
                    text = link["text"].strip()

                    if not text:
                        continue

                    if "/batches/class/" in href:
                        if href not in [x["url"] for x in lectures]:
                            lectures.append({
                                "title": text,
                                "url": href
                            })

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

        lines.extend([
            "",
            "LECTURES",
            "-" * 30,
        ])

        for number, lecture in enumerate(lectures, 1):
            lines.append(f"{number}. {lecture['title']}")
            lines.append(lecture["url"])
            lines.append("")

        lines.append(f"Total Lectures: {len(lectures)}")

        output = BytesIO("\n".join(lines).encode("utf-8"))
        output.name = "selectionway_batch.txt"

        await query.message.reply_document(
            document=output,
            filename="selectionway_batch.txt",
            caption=(
                f"Batch: {batch['name']}\n"
                f"Lecture links found: {len(lectures)}"
            )
        )

    except Exception:
        logger.exception("Batch export failed")
        await query.message.reply_text(
            "Export mein problem aayi. Render logs check karein."
        )


async def logout(update, context):
    user_id = update.effective_user.id

    USER_SESSIONS.pop(user_id, None)
    context.user_data.clear()

    try:
        session_path(user_id).unlink(missing_ok=True)
    except Exception:
        pass

    await close_browser(user_id)

    await update.message.reply_text(
        "SelectionWay session logout ho gaya."
    )


async def cancel_login(update, context):
    user_id = update.effective_user.id

    context.user_data.clear()
    await close_browser(user_id)

    await update.message.reply_text("Login cancelled.")
    return ConversationHandler.END


def get_selectionway_handlers():
    login = ConversationHandler(
        entry_points=[
            CommandHandler("selectionway", selectionway_start)
        ],
        states={
            USERNAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_username
                )
            ],
            OTP: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_otp
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_login)
        ],
        allow_reentry=True,
    )

    return [
        login,
        CallbackQueryHandler(
            export_batch,
            pattern=r"^sw_batch:"
        ),
        CommandHandler("swlogout", logout),
    ]
