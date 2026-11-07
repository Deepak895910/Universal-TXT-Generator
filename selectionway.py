
import os
import logging
from io import BytesIO
from urllib.parse import quote

import httpx
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

USERNAME, PASSWORD, OTP = range(3)

# SelectionWay API configuration
BASE_URL = os.getenv("SW_API_BASE_URL", "").rstrip("/")
LOGIN_PATH = os.getenv("SW_LOGIN_PATH", "")
OTP_PATH = os.getenv("SW_OTP_PATH", "")
BATCHES_PATH = os.getenv("SW_BATCHES_PATH", "")
DETAIL_PATH = os.getenv("SW_BATCH_DETAIL_PATH", "")

# Temporary in-memory sessions (not saved to files)
USER_SESSIONS = {}


class SelectionWayClient:

    def __init__(self, token):
        if not BASE_URL:
            raise RuntimeError("SelectionWay API is not configured")

        self.token = token

    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def url(self, path):
        if not path:
            raise RuntimeError("API endpoint is not configured")

        return f"{BASE_URL}/{path.lstrip('/')}"

    async def login(self, username, password):
        if not LOGIN_PATH:
            raise RuntimeError("Login API endpoint is missing")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.url(LOGIN_PATH),
                json={
                    "username": username,
                    "password": password,
                },
            )

            response.raise_for_status()
            data = response.json()

        # OTP verification required
        if data.get("requires_otp") or data.get("otp_required"):
            return {
                "requires_otp": True,
                "challenge_id": data.get("challenge_id"),
            }

        token = data.get("access_token") or data.get("token")

        if not token:
            raise RuntimeError("Login response has no access token")

        self.token = token
        return {"success": True}

    async def verify_otp(self, otp, challenge_id):
        if not OTP_PATH:
            raise RuntimeError("OTP API endpoint is missing")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.url(OTP_PATH),
                json={
                    "otp": otp,
                    "challenge_id": challenge_id,
                },
            )

            response.raise_for_status()
            data = response.json()

        token = data.get("access_token") or data.get("token")

        if not token:
            raise RuntimeError("OTP response has no access token")

        self.token = token
        return True

    async def get_batches(self):
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.url(BATCHES_PATH),
                headers=self.headers(),
            )

            response.raise_for_status()
            data = response.json()

        if isinstance(data, list):
            return data

        return data.get("batches", data.get("results", []))

    async def get_batch_details(self, batch_id):
        if not DETAIL_PATH:
            raise RuntimeError("Batch detail API is missing")

        path = DETAIL_PATH.replace(
            "{batch_id}", quote(str(batch_id), safe="")
        )

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.url(path),
                headers=self.headers(),
            )

            response.raise_for_status()
            return response.json()


def generate_txt(data):
    lines = [
        "UNIVERSAL TXT GENERATOR",
        "=" * 45,
        f"Batch: {data.get('name', data.get('batch_name', 'Unknown'))}",
        "",
    ]

    subjects = data.get("subjects", [])

    for subject in subjects:
        lines.append(
            f"SUBJECT: {subject.get('name', 'Unknown')}"
        )

        for section in subject.get("sections", []):
            lines.append(
                f"  SECTION: {section.get('name', 'Unknown')}"
            )

            for lecture in section.get("lectures", []):
                title = lecture.get(
                    "title", lecture.get("name", "Unknown")
                )

                lines.append(f"    Lecture: {title}")

                if lecture.get("id") is not None:
                    lines.append(f"    ID: {lecture['id']}")

                if lecture.get("url"):
                    lines.append(f"    Link: {lecture['url']}")

                lines.append("")

        lines.append("")

    return "\n".join(lines)


async def selectionway_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not all([BASE_URL, LOGIN_PATH, BATCHES_PATH, DETAIL_PATH]):
        await update.message.reply_text(
            "SelectionWay API configuration abhi complete nahi hai.\n\n"
            "Official API details milne ke baad Login activate hoga."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🎓 SelectionWay Login\n\n"
        "Apna Username ya Email bhejiye:"
    )

    return USERNAME


async def receive_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data["sw_username"] = update.message.text.strip()

    await update.message.reply_text(
        "Ab apna SelectionWay Password bhejiye.\n\n"
        "Password kisi log ya file mein save nahi kiya jayega."
    )

    return PASSWORD


async def receive_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    username = context.user_data.pop("sw_username", None)
    password = update.message.text

    # Remove password message from chat when possible
    try:
        await update.message.delete()
    except Exception:
        pass

    client = SelectionWayClient(token="")

    try:
        result = await client.login(username, password)

        if result.get("requires_otp"):
            context.user_data["sw_challenge"] = result.get(
                "challenge_id"
            )

            await update.effective_chat.send_message(
                "OTP verification required.\n\n"
                "SelectionWay se mila OTP bhejiye:"
            )

            return OTP

        USER_SESSIONS[update.effective_user.id] = client

        await update.effective_chat.send_message(
            "✅ Login successful!"
        )

        await show_batches(update, context)
        return ConversationHandler.END

    except Exception:
        logger.exception("SelectionWay login failed")
        await update.effective_chat.send_message(
            "Login unsuccessful. API configuration ya credentials "
            "check karein."
        )
        return ConversationHandler.END

    finally:
        password = None


async def receive_otp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    otp = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    client = SelectionWayClient(token="")
    challenge = context.user_data.pop("sw_challenge", None)

    try:
        await client.verify_otp(otp, challenge)

        USER_SESSIONS[update.effective_user.id] = client

        await update.effective_chat.send_message(
            "✅ OTP verified. Login successful!"
        )

        await show_batches(update, context)
        return ConversationHandler.END

    except Exception:
        logger.exception("OTP verification failed")
        await update.effective_chat.send_message(
            "OTP verification failed. Please try again."
        )
        return ConversationHandler.END

    finally:
        otp = None


async def show_batches(update, context):
    user_id = update.effective_user.id
    client = USER_SESSIONS.get(user_id)

    if not client:
        await update.effective_chat.send_message(
            "Please login first using /selectionway"
        )
        return

    try:
        batches = await client.get_batches()

        if not batches:
            await update.effective_chat.send_message(
                "No accessible batches found."
            )
            return

        keyboard = []

        for batch in batches:
            batch_id = batch.get("id")
            name = batch.get("name", "Unnamed Batch")
            category = batch.get(
                "type", batch.get("category", "Available")
            )

            if batch_id is None:
                continue

            keyboard.append([
                InlineKeyboardButton(
                    f"{name} ({category})",
                    callback_data=f"sw_batch:{batch_id}",
                )
            ])

        if not keyboard:
            await update.effective_chat.send_message(
                "Batch list mein valid IDs nahi mile."
            )
            return

        await update.effective_chat.send_message(
            "📚 Available SelectionWay Batches:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception:
        logger.exception("Could not fetch batches")
        await update.effective_chat.send_message(
            "Batch list fetch nahi hui. API details check karein."
        )


async def select_batch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        batch_id = query.data.split(":", 1)[1]
        client = USER_SESSIONS.get(query.from_user.id)

        if not client:
            await query.message.reply_text(
                "Session expired. Please login again."
            )
            return

        await query.edit_message_text(
            "⏳ Batch details fetch ho rahi hain..."
        )

        data = await client.get_batch_details(batch_id)

        txt = generate_txt(data)

        output = BytesIO(txt.encode("utf-8"))
        output.name = "selectionway_batch.txt"

        await query.message.reply_document(
            document=output,
            filename="selectionway_batch.txt",
            caption="✅ Batch TXT ready!",
        )

    except Exception:
        logger.exception("Batch export failed")
        await query.message.reply_text(
            "Batch export failed. API response format check karein."
        )


async def logout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    USER_SESSIONS.pop(user_id, None)

    await update.message.reply_text(
        "You have been logged out from SelectionWay. 🔒"
    )


def get_selectionway_handlers():
    login_handler = ConversationHandler(
        entry_points=[
            CommandHandler("selectionway", selectionway_start)
        ],
        states={
            USERNAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_username,
                )
            ],
            PASSWORD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_password,
                )
            ],
            OTP: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_otp,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_login)
        ],
        allow_reentry=True,
    )

    return [
        login_handler,
        CallbackQueryHandler(
            select_batch,
            pattern=r"^sw_batch:",
        ),
        CommandHandler("swlogout", logout),
    ]


async def cancel_login(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("sw_username", None)
    context.user_data.pop("sw_challenge", None)

    await update.message.reply_text(
        "Login cancelled."
    )

    return ConversationHandler.END
          
