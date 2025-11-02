# main.py — BlajeChatBot (удаляет сообщения "от имени канала" и пишет варн)
# Работает на Render, с python-telegram-bot==21.8 и Flask

import os
import asyncio
import logging
from flask import Flask, jsonify
from telegram import Update
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application, MessageHandler, CommandHandler, ContextTypes, filters
)

# ---------- Логирование ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("BlajeChatBot")

# ---------- Переменные окружения ----------
TOKEN = os.environ.get("TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "1000"))

if not TOKEN:
    raise RuntimeError("❌ Переменная TOKEN не найдена в окружении Render!")

WARNING_TEXT = (
    "⚠️ Сообщения, отправленные от имени канала, запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от личного профиля.\n— Бот Модератор."
)

# ---------- Flask (health-check для Render) ----------
app = Flask(__name__)

@app.route("/")
def index():
    return "BlajeChatBot работает!"

@app.route("/health")
def health():
    return jsonify(ok=True)

# ---------- Telegram обработчики ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text(
            "✅ Бот запущен. Добавьте меня админом в группу обсуждений "
            "и я буду удалять сообщения, отправленные от имени канала."
        )

def is_channel_identity(update: Update) -> bool:
    msg = update.effective_message
    return msg and msg.sender_chat is not None

async def guard_channel_identity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    sender = msg.sender_chat

    log.info("💬 chat_id=%s sender_chat=%s type=%s", chat.id, getattr(sender, "id", None), getattr(sender, "type", None))

    if not is_channel_identity(update):
        return

    try:
        await msg.delete()
        log.info("🗑️ Удалено сообщение от имени канала %s в чате %s", sender.id, chat.id)
    except Forbidden:
        log.warning("❌ Нет прав на удаление сообщений в чате %s", chat.id)
        return
    except BadRequest as e:
        log.warning("⚠️ Ошибка удаления: %s", e)
        return

    # Пишем предупреждение
    try:
        if msg.is_topic_message and msg.message_thread_id:
            await context.bot.send_message(
                chat_id=chat.id,
                text=WARNING_TEXT,
                message_thread_id=msg.message_thread_id
            )
        else:
            await context.bot.send_message(chat_id=chat.id, text=WARNING_TEXT)
    except Exception as e:
        log.warning("Не удалось отправить предупреждение: %s", e)

# ---------- Асинхронный запуск ----------
async def start_bot():
    app_tg = Application.builder().token(TOKEN).concurrent_updates(True).build()
    app_tg.add_handler(CommandHandler("start", cmd_start))
    app_tg.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.SenderChat(True), guard_channel_identity))

    webhook_url = f"{PUBLIC_URL}/telegram/{TOKEN}"
    await app_tg.bot.delete_webhook(drop_pending_updates=True)
    await app_tg.bot.set_webhook(url=webhook_url)
    log.info("🌐 Webhook установлен: %s", webhook_url)

    # Запускаем сервер Telegram в фоне
    await app_tg.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url
    )

def main():
    try:
        asyncio.get_event_loop().run_until_complete(start_bot())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
