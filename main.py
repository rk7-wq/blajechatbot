import os
import logging
import threading
import asyncio
from flask import Flask, request, jsonify

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

# ------------------ ЛОГИ ------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("BlajeChatBot")

# ------------------ ENV -------------------
TOKEN = os.environ["TOKEN"]
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 1000))

# ------------------ Telegram ----------------
# Создаём Application (но НЕ запускаем его здесь)
app_tg = Application.builder().token(TOKEN).build()

# Удаляем сообщения, отправленные от имени канала/группы
async def delete_channel_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    # Сообщение от имени канала/группы имеет sender_chat и нет from_user
    if msg.sender_chat and msg.from_user is None:
        try:
            await msg.delete()
            log.info(
                "Удалено сообщение от имени '%s' в чате %s",
                msg.sender_chat.title,
                msg.chat_id,
            )
        except Exception as e:
            log.error("Ошибка при удалении: %s", e)

app_tg.add_handler(MessageHandler(filters.ALL, delete_channel_posts))

# ------------------ Flask -------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def root():
    return "BlajeChatBot is live!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    # Принимаем JSON от Telegram и передаём его в PTB
    try:
        update = Update.de_json(request.get_json(force=True), app_tg.bot)
        # ВАЖНО: очередь читается Application'ом только когда он стартован!
        app_tg.update_queue.put_nowait(update)
    except Exception as e:
        log.exception("Ошибка обработки webhook: %s", e)
        return jsonify(ok=False), 500
    return jsonify(ok=True), 200

# ------------------ Фоновый цикл PTB -------------------
# Запускаем Telegram Application в своём asyncio-цикле,
# чтобы он обрабатывал update_queue.
def run_ptb_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _startup():
        # Стартуем Application (без polling, он сам обрабатывает очередь)
        await app_tg.initialize()
        await app_tg.start()

        # Переставим webhook на наш Flask-роут
        webhook_url = f"{PUBLIC_URL}/webhook"
        try:
            await app_tg.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            # на всякий случай, если уже не стоит
            pass
        await app_tg.bot.set_webhook(webhook_url)
        log.info("Webhook установлен: %s", webhook_url)

    loop.create_task(_startup())
    loop.run_forever()

def ensure_ptb_started_once():
    # Запускаем поток единожды
    if not getattr(ensure_ptb_started_once, "_started", False):
        t = threading.Thread(target=run_ptb_loop, name="PTB-Thread", daemon=True)
        t.start()
        ensure_ptb_started_once._started = True
        log.info("PTB Application запущен в фоновом потоке")

ensure_ptb_started_once()

# ------------------ ENTRY -------------------
if __name__ == "__main__":
    log.info("Flask на порту %s", PORT)
    app.run(host="0.0.0.0", port=PORT)
