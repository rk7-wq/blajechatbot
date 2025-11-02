# app.py — Telegram Bot (python-telegram-bot v22.x) + Flask webhook
# Render/Gunicorn-ready. Обновления обрабатываются напрямую через process_update.

import os, re, sys, asyncio, logging, threading, atexit
from flask import Flask, request, abort
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

# ── ENV ──────────────────────────────────────────────────────────────────────
TOKEN    = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
SECRET   = os.getenv("WEBHOOK_SECRET", os.urandom(16).hex())
PORT     = int(os.getenv("PORT", "10000"))

DELETE_ALL = os.getenv("DELETE_ALL", "false").lower() == "true"
BANNED_RAW = os.getenv("BANNED", "casino, http://, https://, t.me/")
LOGLEVEL   = os.getenv("LOGLEVEL", "INFO").upper()

if not TOKEN or not BASE_URL:
    print("❌ Set BOT_TOKEN and BASE_URL"); sys.exit(1)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOGLEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("BlajeChatBot")

# ── Бан-слова ────────────────────────────────────────────────────────────────
BANNED_PATTERNS = [re.compile(re.escape(w.strip()), re.I) for w in BANNED_RAW.split(",") if w.strip()]

def is_banned(text: str) -> bool:
    return bool(text) and any(p.search(text) for p in BANNED_PATTERNS)

def is_channel_style_group_message(m) -> bool:
    # Сообщение в группе, отправленное от имени канала / автоперенос из связанного канала
    return bool(getattr(m, "sender_chat", None)) or bool(getattr(m, "is_automatic_forward", False))

def _short(s, n=200):
    if not s: return ""
    return s if len(s) <= n else s[:n] + "…"

# ── Flask ────────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)

# ── PTB + event loop в отдельном потоке ─────────────────────────────────────
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True, name="ptb-loop").start()

application = Application.builder().token(TOKEN).build()

# ── Хэндлеры ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("↪ /start chat=%s user=%s",
             update.effective_chat.id if update.effective_chat else None,
             update.effective_user.id if update.effective_user else None)
    await update.effective_message.reply_text(
        "✅ Бот запущен и слушает вебхук. /ping — быстрая проверка.\n"
        "Добавьте меня админом в группу (право 'Удалять сообщения')."
    )

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("↪ /ping chat=%s", update.effective_chat.id if update.effective_chat else None)
    await update.effective_message.reply_text("✅ Я на связи")

async def on_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    txt = (m.text or m.caption or "")
    log.info("✉️  PRIVATE chat=%s from=%s txt=%r",
             m.chat_id,
             update.effective_user.id if update.effective_user else None,
             _short(txt))
    # Эхо для диагностики (не эхо команд)
    if txt and not txt.startswith("/"):
        try:
            await context.bot.send_message(m.chat_id, f"Эхо: {_short(txt)}")
        except Exception as e:
            log.warning("reply failed chat=%s: %s", m.chat_id, e)

async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m:
        return
    txt = (m.text or m.caption or "")
    log.info("👥 GROUP chat=%s from=%s sender_chat=%s auto_fwd=%s txt=%r",
             m.chat_id,
             update.effective_user.id if update.effective_user else None,
             getattr(m, "sender_chat", None).id if getattr(m, "sender_chat", None) else None,
             getattr(m, "is_automatic_forward", False),
             _short(txt))

    # Удалить всё (тест прав)
    if DELETE_ALL:
        return await try_delete(context, m.chat_id, m.message_id, "DELETE_ALL")

    # Сообщение «от имени канала» в группе
    if is_channel_style_group_message(m):
        return await try_delete(context, m.chat_id, m.message_id, "sender_chat/linked_channel")

    # Бан-слова
    if is_banned(txt):
        return await try_delete(context, m.chat_id, m.message_id, "banned_text")

async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m:
        return
    txt = (m.text or m.caption or "")
    log.info("📣 CHANNEL chat=%s txt=%r", m.chat_id, _short(txt))

    if DELETE_ALL:
        return await try_delete(context, m.chat_id, m.message_id, "DELETE_ALL")
    if is_banned(txt):
        return await try_delete(context, m.chat_id, m.message_id, "banned_text")

async def try_delete(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, reason: str):
    try:
        await ctx.bot.delete_message(chat_id, msg_id)
        log.info("🗑 deleted %s in %s (%s)", msg_id, chat_id, reason)
    except Exception as e:
        log.warning("delete failed %s/%s: %s", chat_id, msg_id, e)

# Регистрируем хэндлеры
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("ping",  cmd_ping))
application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.StatusUpdate.ALL, on_private_message))
application.add_handler(MessageHandler(filters.ChatType.GROUPS  & ~filters.StatusUpdate.ALL, on_group_message))
application.add_handler(MessageHandler(filters.ChatType.CHANNEL & ~filters.StatusUpdate.ALL, on_channel_post))

# ── Webhook ──────────────────────────────────────────────────────────────────
WEBHOOK_PATH = "/webhook"                                # совпадает с тем, что ставим в setWebhook
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

# Запускаем PTB на живом loop-е
asyncio.run_coroutine_threadsafe(setup_webhook(), loop).result(timeout=30)

# ── Flask routes ─────────────────────────────────────────────────────────────
@flask_app.get("/")
def index():
    return {"ok": True, "service": "BlajeChatBot", "webhook": WEBHOOK_URL}

@flask_app.post(WEBHOOK_PATH)
def telegram_webhook():
    # Диагностика источника запроса и секрета
    ua = request.headers.get("User-Agent", "-")
    secret_hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    log.info("➡️  /webhook POST ua=%s secret_match=%s", ua, "YES" if secret_hdr == SECRET else "NO")

    if secret_hdr != SECRET:
        log.warning("Forbidden webhook: wrong secret")
        abort(403)

    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        # КЛЮЧЕВОЕ: обрабатываем апдейт напрямую (без update_queue)
        asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
    except Exception as e:
        log.exception("webhook error: %s", e)
        # даже при ошибке возвращаем 200, чтобы Telegram не дропал вебхук
        return "ok", 200
    return "ok", 200

@flask_app.get("/healthz")
def healthz():
    return {"status": "ok"}

# ── Грациозное завершение PTB ────────────────────────────────────────────────
def _graceful_shutdown():
    try:
        fut = asyncio.run_coroutine_threadsafe(application.stop(), loop)
        fut.result(timeout=10)
    except Exception as e:
        log.warning("graceful stop warn: %s", e)

atexit.register(_graceful_shutdown)

if __name__ == "__main__":
    # Локально: python app.py
    # На Render: используй gunicorn app:flask_app (см. ниже)
    flask_app.run(host="0.0.0.0", port=PORT)
