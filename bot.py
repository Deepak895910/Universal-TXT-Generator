
import os
import json
from io import BytesIO

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SELECTIONWAY_URL = "https://selectionway.com/"


def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎓 SelectionWay", callback_data="selectionway")],
        [InlineKeyboardButton("🌐 Browser Login", callback_data="browser")],
        [InlineKeyboardButton("📱 Telegram Login", callback_data="telegram_login")],
        [InlineKeyboardButton("📄 Generate TXT from JSON", callback_data="json_txt")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Universal TXT Generator! 🚀\n\n"
        "Choose an option:",
        reply_markup=main_menu()
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "selectionway":
        await query.edit_message_text(
            "SelectionWay selected! 🎓\n\n"
            "Choose your login method:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🌐 Browser Login",
                    callback_data="browser"
                )],
                [InlineKeyboardButton(
                    "📱 Telegram Login",
                    callback_data="telegram_login"
                )],
                [InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="home"
                )],
            ])
        )

    elif query.data == "browser":
        await query.message.reply_text(
            "Open SelectionWay and login in your browser.\n"
            "After login, return to this bot.\n\n"
            "Note: Automatic account connection requires "
            "SelectionWay's official authentication integration.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "Open SelectionWay",
                    url=SELECTIONWAY_URL
                )]
            ])
        )

    elif query.data == "telegram_login":
        await query.message.reply_text(
            "Telegram Login selected. 📱\n\n"
            "This login method will be connected after we get "
            "SelectionWay's official API/authentication details.\n\n"
            "Do not send your password or OTP to this bot yet."
        )

    elif query.data == "json_txt":
        context.user_data["waiting_json"] = True
        await query.message.reply_text(
            "Send a JSON file containing your permitted batch metadata.\n\n"
            "I will convert its subjects, sections and lectures into TXT."
        )

    elif query.data == "home":
        await query.message.reply_text(
            "Main Menu:",
            reply_markup=main_menu()
        )


def make_txt(data):
    lines = []

    lines.append("UNIVERSAL TXT GENERATOR")
    lines.append("=" * 40)
    lines.append(f"Batch: {data.get('batch_name', 'Unknown')}")
    lines.append("")

    for subject in data.get("subjects", []):
        lines.append("SUBJECT: " + subject.get("name", "Unknown"))

        for section in subject.get("sections", []):
            lines.append("  SECTION: " + section.get("name", "Unknown"))

            for lecture in section.get("lectures", []):
                lines.append("    Lecture: " + lecture.get("title", "Unknown"))

                if lecture.get("id"):
                    lines.append("    ID: " + str(lecture["id"]))

                if lecture.get("url"):
                    lines.append("    Link: " + lecture["url"])

                lines.append("")

        lines.append("")

    return "\n".join(lines)


async def receive_json(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_json"):
        return

    document = update.message.document

    if not document or not document.file_name.lower().endswith(".json"):
        await update.message.reply_text("Please send a .json file.")
        return

    file = await document.get_file()
    content = await file.download_as_bytearray()

    try:
        data = json.loads(content.decode("utf-8"))
        txt = make_txt(data)

        output = BytesIO(txt.encode("utf-8"))
        output.name = "batch_metadata.txt"

        await update.message.reply_document(
            document=output,
            filename="batch_metadata.txt",
            caption="Your batch TXT file is ready! ✅"
        )

        context.user_data["waiting_json"] = False

    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        await update.message.reply_text(
            "Invalid JSON file. Please check its format."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Main menu\n"
        "/help - Help"
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(
        MessageHandler(filters.Document.ALL, receive_json)
    )

    print("Universal TXT Generator started!")
    app.run_polling()


if __name__ == "__main__":
    main()
          
