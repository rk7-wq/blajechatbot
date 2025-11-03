# main.py — BlajeChatBot для Render (ASGI, 24/7, без сна)
import os
import logging
from quart import Quart, request, abort
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# === КОНФИГ ===
TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("RENDER_EXTERNAL_URL")  # Render ставит автоматически
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOST_PATH}"
SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")

# === ЛОГИ ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("BlajeChatBot")

# === Quart (ASGI) ===
app = Quart(__name__)

# === PTB ===
application = Application.builder().token(TOKEN).build()

# === Хэндлеры ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот работает на Render!\n"
        "Удаляю сообщения от имени каналов в группах.\n"
        "Дайте права: 'Удалять сообщения' и 'Отправлять сообщения'."
    )

async def handle_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not getattr(msg, "sender_chat", None):
        return

    # Белый список (добавь ID каналов, которым можно писать)
    ALLOWED = {-1001234567890}  # ← Замени на нужные ID
    if msg.sender_chat.id in ALLOWED:
        return

    # Кулдаун: не больше 1 предупреждения в 2 сек
    key = (msg.chat_id, getattr(msg, "message_thread_id", 0))
    now = __import__('time').time()
    if not hasattr(handle_group, "last_warn"):
        handle_group.last_warn = {}
    if now - handle_group.last_warn.get(key, 0) < 2:
        pass
    else:
        try:
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text="Сообщения от имени канала запрещены.\nПишите от своего профиля.",
                reply_to_message_id=msg.message_id,
                disable_notification=True
            )
            handle_group.last_warn[key] = now
        except Exception as e:
            log.warning(f"Предупреждение не отправлено: {e}")

    # Удаление
    try:
        await msg.delete()
        log.info(f"Удалено: {msg.sender_chat.title or msg.sender_chat.id}")
    except Exception as e:
        log.error(f"Не удалось удалить: {e}")

# Регистрация хэндлеров
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
        log.info(f"Update processed: {update.update_id}")
    except Exception as e:
        log.exception(f"Webhook error: {e}")
        abort(400)
    return "OK", 200

# === Дополнительные роуты ===
@app.get("/")
async def index():
    return {"status": "ok", "webhook": WEBHOOK_URL, "service": "BlajeChatBot"}

@app.get("/health")
async def health():
    return "OK", 200

# === Установка webhook ===
async def setup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=SECRET,
        drop_pending_updates=True
    )
    log.info(f"Webhook установлен: {WEBHOOK_URL}")

# === Запуск Hypercorn ===
if __name__ == "__main__":
    import hypercorn.asyncio
    from hypercorn.config import Config
    import asyncio

    # Установка webhook
    asyncio.get_event_loop().run_until_complete(setup())

    # Запуск сервера
    config = Config()
    config.bind = [f"0.0.0.0:{os.getenv('PORT', 10000)}"]
    hypercorn.asyncio.serve(app, config)
