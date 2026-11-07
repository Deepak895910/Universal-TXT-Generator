
import os
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from selectionway import get_selectionway_handlers

# ---------------- CONFIGURATION ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ---------------- MAIN MENU ----------------

def main_menu():
    keyboard = [
        [
            InlineKeyboardButton(
                "🎓 SelectionWay",
                callback_data="selectionway",
            )
        ],
        [
            InlineKeyboardButton(
                "ℹ️ Help",
                callback_data="help",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# ---------------- START COMMAND ----------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🚀 Welcome to Universal TXT Generator!\n\n"
        "Select an app to continue:",
        reply_markup=main_menu(),
    )


# ---------------- BUTTON HANDLER ----------------

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.data == "selectionway":
        await query.message.reply_text(
            "🎓 SelectionWay\n\n"
            "Login karne ke liye neeche button dabayein:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔐 Login with Telegram",
                        callback_data="sw_login",
                    )
                ]
            ]),
        )

    elif query.data == "sw_login":
        await query.message.reply_text(
            "SelectionWay login shuru karne ke liye:\n\n"
            "/selectionway"
        )

    elif query.data == "help":
        await query.message.reply_text(
            "📖 Help\n\n"
            "/start - Main Menu\n"
            "/selectionway - SelectionWay Login\n"
            "/swlogout - Logout\n"
            "/cancel - Cancel Login"
        )


# ---------------- ERROR HANDLER ----------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Bot error occurred",
        exc_info=context.error,
    )


# ---------------- MAIN FUNCTION ----------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing!"
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Main commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))

    # Main menu buttons
    app.add_handler(
        CallbackQueryHandler(
            button_handler,
            pattern=r"^(selectionway|sw_login|help)$",
        )
    )

    # SelectionWay handlers
    for handler in get_selectionway_handlers():
        app.add_handler(handler)

    # Error handler
    app.add_error_handler(error_handler)

    logger.info("Universal TXT Generator Bot Started!")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
