
import os
import asyncio
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


# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))


# Start command
async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    keyboard = [
        [
            InlineKeyboardButton(
                "SelectionWay",
                callback_data="sw_login"
            )
        ],
        [
            InlineKeyboardButton(
                "Help",
                callback_data="help"
            )
        ],
    ]

    await update.message.reply_text(
        "Welcome to Universal TXT Generator!\n\n"
        "Select an option below:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# Help command
async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Universal TXT Generator Help\n\n"
        "1. /start - Open main menu\n"
        "2. /selectionway - Start SelectionWay login\n"
        "3. /swlogout - Logout from SelectionWay"
    )


# Button handler
async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if query.data == "sw_login":
        await query.message.reply_text(
            "SelectionWay login shuru karne ke liye:\n"
            "/selectionway type karein."
        )

    elif query.data == "help":
        await query.message.reply_text(
            "Commands:\n"
            "/start - Main menu\n"
            "/selectionway - Login\n"
            "/swlogout - Logout"
        )


# HTTP health server for Render
async def handle_http(reader, writer):
    try:
        request = await asyncio.wait_for(
            reader.readline(),
            timeout=5
        )

        path = request.decode(
            "utf-8", errors="ignore"
        ).split(" ")[1] if request else "/"

        if path in ("/", "/health", "/healthz"):
            body = b"Universal TXT Generator is running"
            status = "200 OK"
        else:
            body = b"Not Found"
            status = "404 Not Found"

        response = (
            f"HTTP/1.1 {status}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8") + body

        writer.write(response)
        await writer.drain()

    except Exception:
        logger.exception("HTTP request error")

    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing!"
        )

    app = Application.builder().token(BOT_TOKEN).build()

    # Telegram commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    # Menu buttons
    app.add_handler(
        CallbackQueryHandler(
            button_handler,
            pattern="^(sw_login|help)$"
        )
    )

    # SelectionWay handlers
    sw_handlers = get_selectionway_handlers()
    for handler in sw_handlers:
        app.add_handler(handler)

    # Error handler
    async def error_handler(update, context):
        logger.error(
            "Telegram error: %s",
            context.error
        )

    app.add_error_handler(error_handler)

    # Start Telegram bot
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Start Render HTTP server
    server = await asyncio.start_server(
        handle_http,
        "0.0.0.0",
        PORT
    )

    logger.info("Bot started successfully!")
    logger.info("HTTP server running on port %s", PORT)

    try:
        async with server:
            await asyncio.Event().wait()
    finally:
        await server.wait_closed()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
