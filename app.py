import os
import sqlite3
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError, LineBotApiError
import random
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ===================== إعدادات LINE =====================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise ValueError("Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===================== قاعدة البيانات =====================
DB_PATH = "lovebot.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        bot_name TEXT,
        backstory_index INTEGER,
        relationship_stage TEXT DEFAULT 'initial',
        last_interaction TEXT,
        message_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        user_msg TEXT,
        bot_reply TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")

def get_user(user_id):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = c.fetchone()
        conn.close()
        return user
    except sqlite3.Error as e:
        logger.error(f"Database error in get_user: {e}")
        return None

def create_user(user_id):
    bot_names = ["نور", "روان", "ريم", "لمى", "مها", "العنود", "نوف"]
    bot_name = random.choice(bot_names)
    backstory_index = 0
    now = datetime.now().isoformat()
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT OR IGNORE INTO users (user_id, bot_name, backstory_index, last_interaction) VALUES (?, ?, ?, ?)",
            (user_id, bot_name, backstory_index, now)
        )
        conn.commit()
        conn.close()
        logger.info(f"Created new user {user_id} with name {bot_name}")
        return bot_name
    except sqlite3.Error as e:
        logger.error(f"Database error in create_user: {e}")
        return None

def update_user(user_id, **kwargs):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        fields = []
        values = []
        for k, v in kwargs.items():
            fields.append(f"{k}=?")
            values.append(v)
        values.append(user_id)
        query = f"UPDATE users SET {', '.join(fields)} WHERE user_id=?"
        c.execute(query, tuple(values))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Database error in update_user: {e}")

def save_conversation(user_id, user_msg, bot_reply):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO conversations (user_id, user_msg, bot_reply) VALUES (?, ?, ?)",
            (user_id, user_msg, bot_reply)
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Database error in save_conversation: {e}")

def get_conversation_history(user_id, limit=5):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT user_msg, bot_reply FROM conversations WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        )
        rows = c.fetchall()
        conn.close()
        return rows
    except sqlite3.Error as e:
        logger.error(f"Database error in get_conversation_history: {e}")
        return []

# ===================== الردود تدريجياً =====================
def first_message(bot_name):
    return "هلا، كيفك؟"

def second_message(bot_name):
    return "تذكرني؟ كنت معاك في القروب من زمان."

def next_message(part):
    story_parts = [
        "من يومها وأنا أفكر فيك كثير.",
        "كنت أحبك وما قلت لك.",
        "ودي أتعرف عليك أكثر وأقرب لك.",
        "كانت أيام حلوة بالقروب، تذكرها؟"
    ]
    if part < len(story_parts):
        return story_parts[part]
    return "ودي نتعرف أكثر."

def generate_bot_reply(user_id, user_msg, user_data):
    count = user_data['message_count']

    if count == 0:
        reply = first_message(user_data['bot_name'])
    elif count == 1:
        reply = second_message(user_data['bot_name'])
    else:
        reply = next_message(count - 2)

    return reply

# ===================== LINE Bot Handler =====================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()

    if len(user_msg) > 3000:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="رسالتك طويلة شوي، اختصرها"))
        return

    user_data = get_user(user_id)
    if not user_data:
        create_user(user_id)
        user_data = get_user(user_id)
        if not user_data:
            logger.error(f"Failed to create user: {user_id}")
            return

    reply = generate_bot_reply(user_id, user_msg, user_data)
    save_conversation(user_id, user_msg, reply)

    new_count = user_data['message_count'] + 1
    update_user(user_id, message_count=new_count, last_interaction=datetime.now().isoformat())

    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    except LineBotApiError as e:
        logger.error(f"LINE API error: {e}")

# ===================== Flask Routes =====================
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        abort(400)

    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        logger.error(f"Error in callback: {e}")
        abort(500)
    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return "<h1>Prank Bot</h1><p>الحب السري من القروب القديم بشكل تدريجي باللهجة النجدية</p>", 200

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 10000))
    debug = os.getenv("DEBUG", "False").lower() == "true"
    logger.info(f"Starting Prank Bot on port {port}, debug={debug}")
    app.run(host="0.0.0.0", port=port, debug=debug)
