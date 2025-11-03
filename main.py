# main.py — BlajeChatBot (Render, Python 3.11 + Quart 0.19.8)
import os
import logging
from quart import Quart, request, abort
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# === КОНФИГ ===
TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"
SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")

# === ТЕКСТ ПРЕДУПРЕЖДЕНИЯ ===
WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего личного профиля.\n"
    "Бот Модератор."
)

# === ЛОГИ ===
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("BlajeChatBot")

# === Quart + ФИКС ОШИБКИ ===
app = Quart(__name__)
app.config['PROVIDE_AUTOMATIC_OPTIONS'] = False  # ← ФИКС KeyError

# === PTB ===
application = Application.builder().token(TOKEN).build()

# === Анти-флуд: хранит время последнего предупреждения ===
last_warning = {}

# === Хэндлеры ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот запущен! Удаляю сообщения от каналов.")

async def handle_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not getattr(msg, "sender_chat", None):
        return

    chat_id = msg.chat_id
    thread_id = getattr(msg, "message_thread_id", 0)
    key = (chat_id, thread_id)
    now = __import__('time').time()

    # === 1. Удаляем сообщение от канала ===
    try:
        await msg.delete()
        log.info(f"Удалено: {msg.sender_chat.title} | {chat_id}")
    except Exception as e:
        log.error(f"Не удалось удалить: {e}")

    # === 2. Отправляем предупреждение (не чаще 1 раза в 2 сек) ===
    if now - last_warning.get(key, 0) >= 2:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=WARNING_TEXT,
                reply_to_message_id=None,
                disable_notification=True,
                message_thread_id=thread_id if thread_id else None
            )
            last_warning[key] = now
        except Exception as e:
            log.warning(f"Не удалось отправить предупреждение: {e}")

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.ChatType.GROUPS, handle_group))

# === Webhook ===
@app.post(WEBHOOK_PATH)
async def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET:
        abort(403)
    try:
        data = await request.get_json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        log.info(f"Update: {update.update_id}")
    except Exception as e:
        log.exception("Webhook error")
        abort(400)
    return "OK", 200

@app.get("/health")
async def health():
    return "OK", 200

# === Запуск ===
async def setup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=SECRET,
        drop_pending_updates=True
    )
    log.info(f"Webhook: {WEBHOOK_URL}")

if __name__ == "__main__":
    import hypercorn.asyncio
    from hypercorn.config import Config
    import asyncio
    asyncio.run(setup())
    config = Config()
    config.bind = [f"0.0.0.0:{os.getenv('PORT', 10000)}"]
    hypercorn.asyncio.serve(app, config)
