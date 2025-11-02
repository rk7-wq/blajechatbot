# main.py — Telegram Bot (python-telegram-bot v22.x) + Flask webhook
# Работает на Render/Gunicorn. Обрабатывает:
#  • Группы/супергруппы (в т.ч. сообщения "от имени канала" → sender_chat/auto-forward)
#  • Каналы
#  • Бан-слова / тестовый режим DELETE_ALL
#  • Команды /start и /ping
#
# ENV (Render → Settings → Environment):
#   BOT_TOKEN        = <токен бота из BotFather>
#   BASE_URL         = https://<ваш-сервис>.onrender.com
#   WEBHOOK_SECRET   = любая_строка
#   DELETE_ALL       = false  (или true для тотального удаления, удобно для теста)
#   BANNED           = "casino, http://, https://, t.me/"  (по желанию)

import os, re, sys, asyncio, logging, threading, atexit
from flask import Flask, request, abort
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

# ── ENV ──────────────────────────────────────────────────────────────────────
TOKEN  = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
SECRET = os.getenv("WEBHOOK_SECRET", os.urandom(16).hex())
PORT   = int(os.getenv("PORT", "10000"))

DELETE_ALL = os.getenv("DELETE_ALL", "false").lower() == "true"
BANNED_RAW = os.getenv("BANNED", "casino, http://, https://, t.me/")

if not TOKEN or not BASE_URL:
    print("❌ Set BOT_TOKEN and BASE_URL")
    sys.exit(1)

BANNED = [re.compile(re.escape(w.strip()), re.I) for w in BANNED_RAW.split(",") if w.strip()]

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("BlajeChatBot")

# ── Flask ────────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)

# ── PTB + отдельный event loop ───────────────────────────────────────────────
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True, name="ptb-loop").start()
application = Application.builder().token(TOKEN).build()

# ── Хелперы модерации ────────────────────────────────────────────────────────
def is_banned(text: str) -> bool:
    return bool(text) and any(p.search(text) for p in BANNED)

def is_channel_style_group_message(m) -> bool:
    # Сообщение в группе, отправленное "от имени канала" или авто-пересланное из связанного канала
    return bool(getattr(m, "sender_chat", None)) or bool(getattr(m, "is_automatic_forward", False))

async def try_delete(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, reason: str):
    try:
        await ctx.bot.delete_message(chat_id, msg_id)
        log.info("🗑 deleted %s in %s (%s)", msg_id, chat_id, reason)
    except Exception as e:
        log.warning("delete failed %s/%s: %s", chat_id, msg_id, e)

# ── Хэндлеры ─────────────────────────────────────────────────────────────────
async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m:
        return

    if DELETE_ALL:
        return await try_delete(context, m.chat_id, m.message_id, "DELETE_ALL")

    if is_channel_style_group_message(m):
        return await try_delete(context, m.chat_id, m.message_id, "sender_chat/linked_channel")

    text = (m.text or m.caption or "")[:4096]
    if is_banned(text):
        return await try_delete(context, m.chat_id, m.message_id, "banned_text")

async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m:
        return
    if DELETE_ALL:
        return await try_delete(context, m.chat_id, m.message_id, "DELETE_ALL")
    text = (m.text or m.caption or "")[:4096]
    if is_banned(text):
        return await try_delete(context, m.chat_id, m.message_id, "banned_text")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "✅ Бот запущен.\n"
        "Добавьте меня админом группы (с правом удалять сообщения), "
        "и я буду чистить посты, отправленные от имени канала."
    )
    await update.effective_message.reply_text(msg)

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("✅ Я на связи")

# ── Регистрация хэндлеров ────────────────────────────────────────────────────
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("ping",  cmd_ping))
application.add_handler(MessageHandler(filters.ChatType.GROUPS   & ~filters.StatusUpdate.ALL, on_group_message))
application.add_handler(MessageHandler(filters.ChatType.CHANNEL  & ~filters.StatusUpdate.ALL, on_channel_post))

# ── Вебхук ───────────────────────────────────────────────────────────────────
WEBHOOK_PATH = "/webhook"  # соответствует тому, что выставляли в setWebhook
WEBHOOK_URL  = f"{BASE_URL}{WEBHOOK_PATH}"

async def setup_webhook():
    await application.initialize()
    await application.start()
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("delete_webhook warn: %s", e)
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=SECRET,
        allowed_updates=[
            "message","edited_message",
            "channel_post","edited_channel_post",
            "chat_member","my_chat_member",
        ],
        max_connections=40,
    )
    log.info("✅ Webhook set: %s", WEBHOOK_URL)

# Запускаем настройку PTB на живом loop-е
asyncio.run_coroutine_threadsafe(setup_webhook(), loop).result(timeout=30)

# ── Flask маршруты ───────────────────────────────────────────────────────────
@flask_app.get("/")
def index():
    return {"ok": True, "service": "BlajeChatBot", "webhook": WEBHOOK_URL}

@flask_app.post(WEBHOOK_PATH)
def telegram_webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET:
        abort(403)
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        application.update_queue.put_nowait(update)  # мгновенный 200 — обработка в фоне
    except Exception as e:
        log.exception("webhook error: %s", e)
    return "ok", 200

@flask_app.get("/healthz")
def healthz():
    return {"status": "ok"}

# Грациозное завершение PTB, чтобы не было "Task was destroyed..."
def _graceful_shutdown():
    try:
        fut = asyncio.run_coroutine_threadsafe(application.stop(), loop)
        fut.result(timeout=10)
    except Exception as e:
        log.warning("graceful stop warn: %s", e)

atexit.register(_graceful_shutdown)

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT)
