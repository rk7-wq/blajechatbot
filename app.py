# app.py — Telegram webhook (PTB v22) + Flask + расширенная диагностика
import os, re, sys, asyncio, logging, threading, atexit
from flask import Flask, request, abort
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

# ===== ENV =====
TOKEN   = os.getenv("BOT_TOKEN", "").strip()
BASE_URL= os.getenv("BASE_URL", "").rstrip("/")
SECRET  = os.getenv("WEBHOOK_SECRET", os.urandom(16).hex())
PORT    = int(os.getenv("PORT", "10000"))

DELETE_ALL  = os.getenv("DELETE_ALL", "false").lower() == "true"
BANNED_RAW  = os.getenv("BANNED", "casino, http://, https://, t.me/")
LOGLEVEL    = os.getenv("LOGLEVEL", "INFO").upper()

if not TOKEN or not BASE_URL:
    print("❌ Set BOT_TOKEN and BASE_URL"); sys.exit(1)

# ===== LOGGING =====
logging.basicConfig(level=getattr(logging, LOGLEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("BlajeChatBot")

# ===== COMPILE BAN PATTERNS =====
BANNED = [re.compile(re.escape(w.strip()), re.I) for w in BANNED_RAW.split(",") if w.strip()]

# ===== FLASK =====
flask_app = Flask(__name__)

# ===== PTB + EVENT LOOP THREAD =====
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True, name="ptb-loop").start()
application = Application.builder().token(TOKEN).build()

# ===== HELPERS =====
def is_banned(text: str) -> bool:
    return bool(text) and any(p.search(text) for p in BANNED)

def is_channel_style_group_message(m) -> bool:
    return bool(getattr(m, "sender_chat", None)) or bool(getattr(m, "is_automatic_forward", False))

async def safe_reply(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    try:
        await ctx.bot.send_message(chat_id, text)
    except Exception as e:
        log.warning("reply failed chat=%s: %s", chat_id, e)

async def try_delete(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, reason: str):
    try:
        await ctx.bot.delete_message(chat_id, msg_id)
        log.info("🗑 deleted %s in %s (%s)", msg_id, chat_id, reason)
    except Exception as e:
        log.warning("delete failed %s/%s: %s", chat_id, msg_id, e)

def _short(s, n=120):
    if not s: return ""
    return s if len(s)<=n else s[:n]+"…"

# ===== HANDLERS =====
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("↪ /start from chat=%s user=%s", update.effective_chat.id if update.effective_chat else None,
             update.effective_user.id if update.effective_user else None)
    await update.effective_message.reply_text("✅ Бот запущен и слушает вебхук. Команда /ping проверяет связь.")

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("↪ /ping from chat=%s", update.effective_chat.id if update.effective_chat else None)
    await update.effective_message.reply_text("✅ Я на связи")

async def on_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    txt = (m.text or m.caption or "")
    log.info("✉️  PRIVATE chat=%s from=%s txt=%r", m.chat_id,
             update.effective_user.id if update.effective_user else None, _short(txt))
    # echo для диагностики
    if txt and not txt.startswith("/"):
        await safe_reply(context, m.chat_id, f"Эхо: {_short(txt)}")

async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    txt = (m.text or m.caption or "")
    log.info("👥 GROUP chat=%s from=%s sender_chat=%s auto_fwd=%s txt=%r",
             m.chat_id,
             update.effective_user.id if update.effective_user else None,
             getattr(m, "sender_chat", None).id if getattr(m, "sender_chat", None) else None,
             getattr(m, "is_automatic_forward", False),
             _short(txt))
    if DELETE_ALL:
        return await try_delete(context, m.chat_id, m.message_id, "DELETE_ALL")
    if is_channel_style_group_message(m):
        return await try_delete(context, m.chat_id, m.message_id, "sender_chat/linked_channel")
    if is_banned(txt):
        return await try_delete(context, m.chat_id, m.message_id, "banned_text")

async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    txt = (m.text or m.caption or "")
    log.info("📣 CHANNEL chat=%s txt=%r", m.chat_id, _short(txt))
    if DELETE_ALL:
        return await try_delete(context, m.chat_id, m.message_id, "DELETE_ALL")
    if is_banned(txt):
        return await try_delete(context, m.chat_id, m.message_id, "banned_text")

# ===== REGISTER HANDLERS =====
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("ping",  cmd_ping))
application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.StatusUpdate.ALL, on_private_message))
application.add_handler(MessageHandler(filters.ChatType.GROUPS  & ~filters.StatusUpdate.ALL, on_group_message))
application.add_handler(MessageHandler(filters.ChatType.CHANNEL & ~filters.StatusUpdate.ALL, on_channel_post))

# ===== WEBHOOK SETUP =====
WEBHOOK_PATH = "/webhook"                    # должен совпадать с getWebhookInfo
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
            "message","edited_message","channel_post","edited_channel_post","chat_member","my_chat_member"
        ],
        max_connections=40,
    )
    log.info("✅ Webhook set: %s", WEBHOOK_URL)

# Запускаем PTB на живом loop-е
asyncio.run_coroutine_threadsafe(setup_webhook(), loop).result(timeout=30)

# ===== FLASK ROUTES =====
@flask_app.get("/")
def index():
    return {"ok": True, "service": "BlajeChatBot", "webhook": WEBHOOK_URL}

@flask_app.post(WEBHOOK_PATH)
def telegram_webhook():
    ua = request.headers.get("User-Agent", "-")
    secret_hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    matched = "YES" if secret_hdr == SECRET else "NO"
    log.info("➡️  /webhook POST ua=%s secret_match=%s", ua, matched)

    if secret_hdr != SECRET:
        # Специально логируем mismatched secret, чтобы сразу видно было причину 403
        log.warning("Forbidden webhook: wrong secret")
        abort(403)

    try:
        data = request.get_json(force=True)
        # Мини-лог типа апдейта до постановки в очередь
        log.debug("raw update keys: %s", list(data.keys()) if isinstance(data, dict) else type(data))
        update = Update.de_json(data, application.bot)
        application.update_queue.put_nowait(update)  # мгновенный ответ Telegram
    except Exception as e:
        log.exception("webhook error: %s", e)
        # даже при ошибке возвращаем 200, чтобы TG не отрубил вебхук
        return "ok", 200
    return "ok", 200

@flask_app.get("/healthz")
def healthz():
    return {"status": "ok"}

# Грациозная остановка PTB
def _graceful_shutdown():
    try:
        fut = asyncio.run_coroutine_threadsafe(application.stop(), loop)
        fut.result(timeout=10)
    except Exception as e:
        log.warning("graceful stop warn: %s", e)

atexit.register(_graceful_shutdown)

if __name__ == "__main__":
    # На Render используй: gunicorn app:flask_app
    flask_app.run(host="0.0.0.0", port=PORT)
