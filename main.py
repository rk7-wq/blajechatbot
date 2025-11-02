# main.py
# Telegram Bot (PTB v21) + Flask webhook
# Под Render / GitHub / PythonAnywhere / Railway и др.

import os
import re
import sys
import asyncio
import logging
import threading
from flask import Flask, request, abort
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ChannelPostHandler,
    ContextTypes,
    filters,
)

# ─── Настройки окружения ─────────────────────────────────────────────────────
TOKEN = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # https://ваш-домен.onrender.com
SECRET = os.getenv("WEBHOOK_SECRET", os.urandom(16).hex())
PORT = int(os.getenv("PORT", "10000"))

# Тестовый режим удаления всех сообщений
DELETE_ALL = os.getenv("DELETE_ALL", "false").lower() == "true"

# Стоп-слова через запятую
BANNED_RAW = os.getenv("BANNED", "casino, http://, https://, t.me/")
BANNED = [
    re.compile(re.escape(word.strip()), flags=re.I)
    for word in BANNED_RAW.split(",")
    if word.strip()
]

if not TOKEN or not BASE_URL:
    print("❌ ERROR: установите BOT_TOKEN и BASE_URL в настройках окружения")
    sys.exit(1)

# ─── Логирование ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("BlajeChatBot")

# ─── Flask приложение ────────────────────────────────────────────────────────
flask_app = Flask(__name__)

# ─── Telegram Bot Application (PTB) ──────────────────────────────────────────
loop = asyncio.new_event_loop()
thread = threading.Thread(target=loop.run_forever, daemon=True)
thread.start()

application = Application.builder().token(TOKEN).build()

# ─── Функции модерации ───────────────────────────────────────────────────────
def is_banned_text(text: str) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in BANNED)

async def try_delete(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, reason: str):
    try:
        await ctx.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        log.info(f"🗑 Удалено сообщение {msg_id} в {chat_id} ({reason})")
    except Exception as e:
        log.warning(f"⚠️ Ошибка удаления {chat_id}/{msg_id}: {e}")

# ─── Хэндлеры ────────────────────────────────────────────────────────────────
async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    if DELETE_ALL:
        await try_delete(context, msg.chat_id, msg.message_id, "DELETE_ALL")
        return
    text = (msg.text or msg.caption or "")[:4096]
    if is_banned_text(text):
        await try_delete(context, msg.chat_id, msg.message_id, "banned_text")

async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.effective_message
    if not post:
        return
    if DELETE_ALL:
        await try_delete(context, post.chat_id, post.message_id, "DELETE_ALL")
        return
    text = (post.text or post.caption or "")[:4096]
    if is_banned_text(text):
        await try_delete(context, post.chat_id, post.message_id, "banned_text")

# Регистрируем обработчики
application.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, on_group_message))
application.add_handler(ChannelPostHandler(on_channel_post))

# ─── Настройка вебхука ───────────────────────────────────────────────────────
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

async def setup_webhook():
    await application.initialize()
    await application.start()
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning(f"delete_webhook warn: {e}")
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=SECRET,
        allowed_updates=[
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "chat_member",
            "my_chat_member",
        ],
        max_connections=40,
    )
    log.info(f"✅ Вебхук установлен: {WEBHOOK_URL}")

def start_bot_async():
    fut = asyncio.run_coroutine_threadsafe(setup_webhook(), loop)
    fut.result(timeout=30)

start_bot_async()

# ─── Flask маршруты ──────────────────────────────────────────────────────────
@flask_app.get("/")
def index():
    return {"ok": True, "service": "BlajeChatBot", "webhook": WEBHOOK_URL}

@flask_app.post(WEBHOOK_PATH)
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET:
        abort(403)
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        application.update_queue.put_nowait(update)
    except Exception as e:
        log.exception(f"Webhook error: {e}")
    return "ok", 200

@flask_app.get("/healthz")
def healthz():
    return {"status": "ok"}

# ─── Запуск ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT)
