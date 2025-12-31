# =========================
# Smart Assistant Bot v2.8
# Production Ready
# =========================

import os
import re
import time
import sqlite3
import threading
import logging
import warnings
from queue import Queue
from datetime import datetime, timedelta
from collections import defaultdict

from flask import Flask, request, abort, jsonify
from dotenv import load_dotenv

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent

# -------------------------
# Warnings
# -------------------------
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
import google.generativeai as genai

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("BOT")

# -------------------------
# App Init
# -------------------------
load_dotenv()
app = Flask(__name__)

# -------------------------
# ENV
# -------------------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

GEMINI_KEYS = [k for k in [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
    os.getenv("GEMINI_API_KEY_4")
] if k]

MAX_DAILY_MESSAGES = int(os.getenv("MAX_DAILY_MESSAGES", 100))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", 2))
PORT = int(os.getenv("PORT", 5000))

BOT_NAME = "Smart Assistant"
BOT_VERSION = "2.8"
BOT_CREATOR = "عبير الدوسري"
BOT_YEAR = "2025"

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise RuntimeError("Missing LINE credentials")

if not GEMINI_KEYS:
    raise RuntimeError("Missing Gemini API keys")

# -------------------------
# LINE
# -------------------------
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# -------------------------
# Database
# -------------------------
DB_PATH = "chatbot.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            first_seen TEXT,
            last_seen TEXT,
            msg_count INTEGER,
            daily_count INTEGER,
            daily_reset TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info("DB ready")

init_db()

# -------------------------
# Rate Limiter
# -------------------------
class RateLimiter:
    def __init__(self):
        self.data = defaultdict(list)

    def allow(self, user_id):
        now = time.time()
        self.data[user_id] = [t for t in self.data[user_id] if now - t < 60]
        self.data[user_id] = self.data[user_id][-5:]
        if self.data[user_id] and now - self.data[user_id][-1] < RATE_LIMIT_SECONDS:
            return False
        self.data[user_id].append(now)
        return True

rate_limiter = RateLimiter()

# -------------------------
# Gemini Manager
# -------------------------
MODELS = ["gemini-1.5-flash", "gemini-1.5-flash-8b"]

GEN_CONFIG = {
    "temperature": 0.8,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 500,
}

SAFETY = [{"category": c, "threshold": "BLOCK_NONE"} for c in [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT"
]]

class GeminiManager:
    def __init__(self):
        self.key = 0
        self.model = 0

    def next(self):
        k = GEMINI_KEYS[self.key]
        m = MODELS[self.model]
        self.model = (self.model + 1) % len(MODELS)
        if self.model == 0:
            self.key = (self.key + 1) % len(GEMINI_KEYS)
        return k, m

gemini = GeminiManager()

# -------------------------
# Helpers
# -------------------------
def detect_lang(text):
    return "ar" if re.search(r"[\u0600-\u06FF]", text) else "en"

def clean(text):
    return re.sub(r"\s+", " ", text.strip())

# -------------------------
# Users / Chats
# -------------------------
def save_user(user_id):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    today = datetime.now().date().isoformat()

    c.execute("SELECT daily_reset FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()

    if row:
        if row["daily_reset"] != today:
            c.execute("""
                UPDATE users
                SET last_seen=?, msg_count=msg_count+1, daily_count=1, daily_reset=?
                WHERE user_id=?
            """, (now, today, user_id))
        else:
            c.execute("""
                UPDATE users
                SET last_seen=?, msg_count=msg_count+1, daily_count=daily_count+1
                WHERE user_id=?
            """, (now, user_id))
    else:
        c.execute("""
            INSERT INTO users VALUES (?, ?, ?, 1, 1, ?)
        """, (user_id, now, now, today))

    conn.commit()
    conn.close()

def daily_allowed(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT daily_count, daily_reset FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return True
    if row["daily_reset"] != datetime.now().date().isoformat():
        return True
    return row["daily_count"] < MAX_DAILY_MESSAGES

def save_chat(user_id, role, text):
    conn = get_db()
    conn.execute(
        "INSERT INTO chats VALUES (NULL, ?, ?, ?, ?)",
        (user_id, role, text, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_history(user_id, limit=4):
    conn = get_db()
    cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
    conn.execute("DELETE FROM chats WHERE timestamp < ?", (cutoff,))
    rows = conn.execute("""
        SELECT role, content FROM chats
        WHERE user_id=? ORDER BY id DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.commit()
    conn.close()
    return list(reversed(rows))

# -------------------------
# AI
# -------------------------
def ai_reply(user_id, text):
    lang = detect_lang(text)
    history = get_history(user_id)

    ctx = ""
    for h in history:
        ctx += f"{h['role']}: {h['content']}\n"

    system = "أجب باختصار." if lang == "ar" else "Answer briefly."
    prompt = f"{system}\n{ctx}\nUser: {text}\nAssistant:"

    for _ in range(4):
        try:
            key, model = gemini.next()
            genai.configure(api_key=key)
            ai = genai.GenerativeModel(
                model,
                generation_config=GEN_CONFIG,
                safety_settings=SAFETY
            )
            res = ai.generate_content(prompt, request_options={"timeout": 8})
            if res and res.text:
                return clean(res.text)[:1200]
        except Exception as e:
            logger.warning(e)
            time.sleep(0.3)

    return "عذرًا، حدث خطأ."

# -------------------------
# Queue Workers
# -------------------------
queue = Queue()

def worker():
    while True:
        user_id, msg = queue.get()
        try:
            reply = ai_reply(user_id, msg)
            save_chat(user_id, "assistant", reply)
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).push_message(
                    PushMessageRequest(to=user_id, messages=[TextMessage(text=reply)])
                )
        except Exception as e:
            logger.error(e)
        finally:
            queue.task_done()

for _ in range(3):
    threading.Thread(target=worker, daemon=True).start()

# -------------------------
# LINE Events
# -------------------------
@handler.add(FollowEvent)
def follow(event):
    save_user(event.source.user_id)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(
                to=event.source.user_id,
                messages=[TextMessage(text="👋 مرحبًا بك!")]
            )
        )

@handler.add(MessageEvent, message=TextMessageContent)
def message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    if not text or len(text) > 3000:
        return
    if not rate_limiter.allow(user_id):
        return
    if not daily_allowed(user_id):
        MessagingApi(ApiClient(configuration)).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="⚠️ وصلت للحد اليومي")]
            )
        )
        return

    save_user(user_id)
    save_chat(user_id, "user", text)
    queue.put((user_id, text))

    MessagingApi(ApiClient(configuration)).reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text="🤔 جاري التفكير...")]
        )
    )

# -------------------------
# Routes
# -------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "running", "bot": BOT_NAME, "version": BOT_VERSION})

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
