import os
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# =========================
# Настройки логгера
# =========================
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("BlajeChatBot")

# =========================
# Переменные окружения
# =========================
TOKEN = os.environ["TOKEN"]
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 1000))

# =========================
# Инициализация Flask и Telegram
# =========================
app = Flask(__name__)
tg_app = Application.builder().token(TOKEN).build()

# =========================
# Основная логика: удалять сообщения от имени канала
# =========================
async def delete_channel_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg and msg.sender_chat and not msg.from_user:
        try:
            await msg.delete()
            log.info(f"Удалено сообщение от имени канала {msg.sender_chat.title}")
        except Exception as e:
            log.error(f"Ошибка при удалении: {e}")

tg_app.add_handler(MessageHandler(filters.ALL, delete_channel_posts))

# =========================
# Flask маршрут для Telegram webhook
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():
    if request.method == "POST":
        try:
            update = Update.de_json(request.get_json(force=True), tg_app.bot)
            tg_app.update_queue.put_nowait(update)
        except Exception as e:
            log.error(f"Ошибка в webhook: {e}")
        return "ok", 200
    return "method not allowed", 405

# =========================
# Flask маршрут для проверки
# =========================
@app.route("/", methods=["GET"])
def home():
    return "BlajeChatBot is live!", 200

# =========================
# Запуск Flask + установка вебхука
# =========================
if __name__ == "__main__":
    async def main():
        webhook_url = f"{PUBLIC_URL}/webhook"
        log.info(f"Устанавливаю webhook: {webhook_url}")
        await tg_app.bot.delete_webhook()
        await tg_app.bot.set_webhook(webhook_url)
        log.info("Webhook установлен, бот готов получать обновления.")

    import asyncio
    asyncio.get_event_loop().run_until_complete(main())

    log.info(f"Flask запущен на порту {PORT}")
    app.run(host="0.0.0.0", port=PORT)
