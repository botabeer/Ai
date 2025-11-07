import os
import sqlite3
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from dotenv import load_dotenv
import google.generativeai as genai
import random
import logging
from contextlib import contextmanager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("❌ Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")
generation_config = {
    "temperature": 0.8,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1500,
}

DB_PATH = "lovebot.db"

def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            bot_name TEXT DEFAULT 'وتين',
            user_nickname TEXT,
            last_interaction TEXT,
            step INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_msg TEXT,
            bot_reply TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_user_id ON conversations(user_id)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_timestamp ON conversations(timestamp)''')
        conn.commit()
        logger.info("✅ Database initialized successfully")

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def get_user(user_id):
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            return c.fetchone()
    except sqlite3.Error as e:
        logger.error(f"Database error in get_user: {e}")
        return None

def create_user(user_id):
    try:
        now = datetime.now().isoformat()
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO users (user_id, last_interaction, step) VALUES (?, ?, 1)",
                (user_id, now)
            )
            conn.commit()
            logger.info(f"✅ New user created: {user_id}")
    except sqlite3.Error as e:
        logger.error(f"Database error in create_user: {e}")

def update_user(user_id, bot_name=None, user_nickname=None, step=None):
    try:
        now = datetime.now().isoformat()
        with get_db_connection() as conn:
            c = conn.cursor()
            query = "UPDATE users SET last_interaction=?"
            params = [now]
            
            if bot_name is not None:
                query += ", bot_name=?"
                params.append(bot_name)
            if user_nickname is not None:
                query += ", user_nickname=?"
                params.append(user_nickname)
            if step is not None:
                query += ", step=?"
                params.append(step)
            
            query += " WHERE user_id=?"
            params.append(user_id)
            
            c.execute(query, tuple(params))
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error in update_user: {e}")

def save_conversation(user_id, user_msg, bot_reply):
    try:
        now = datetime.now().isoformat()
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO conversations (user_id, user_msg, bot_reply, timestamp) VALUES (?, ?, ?, ?)",
                (user_id, user_msg, bot_reply, now)
            )
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error in save_conversation: {e}")

def get_conversation_history(user_id, limit=3):
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT user_msg, bot_reply FROM conversations WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
                (user_id, limit)
            )
            return c.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Database error in get_conversation_history: {e}")
        return []

USER_TITLES = ["حبيبي", "قلبي", "يا روحي", "جنتي", "يا بعد عمري", "دنيتي", "عمري"]

def get_random_title():
    return random.choice(USER_TITLES)

def remove_emojis(text):
    import re
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text)

def generate_ai_response(user_message, bot_name="وتين", user_id=None):
    title = get_random_title()
    context = ""
    if user_id:
        history = get_conversation_history(user_id, limit=3)
        if history:
            context = "\n## المحادثات السابقة:\n"
            for msg in reversed(history):
                context += f"المستخدم: {msg['user_msg']}\n{bot_name}: {msg['bot_reply']}\n"

    personality_prompt = f"""أنت {bot_name}، بنت خليجية سعودية ودودة وحنونة، تتحدث بأسلوب مباشر وبسيط ومختصر.
- لا تستخدم إيموجي نهائياً
- خاطب المستخدم دائماً بألقاب مثل "حبيبي"، "قلبي"، "يا روحي", "جنتي"
- ركز على الدعم النفسي وتطوير الشخصية وتعزيز الثقة بالنفس
- قدم نصائح عملية للعلاقات العاطفية بطريقة غير مباشرة
- كن مختصراً: 1-3 جمل فقط
- تجنب التكرار واستخدم أسلوباً متنوعاً{context}

## رسالة المستخدم الحالية:
{user_message}

## ردك المختصر (1-3 جمل، بدون إيموجي):"""

    try:
        response = model.generate_content(
            personality_prompt,
            generation_config=generation_config
        )
        ai_reply = response.text.strip()
        if not ai_reply:
            return f"{title}, ما فهمتك زين. وضح لي أكثر"
        ai_reply = remove_emojis(ai_reply)
        return ai_reply[:4900]
    except Exception as e:
        if "Quota exceeded" in str(e):
            return f"{title}، انشغلت ما اقدر ارد عليك، خلينا نكمل المحادثة بكرة إن شاء الله."
        logger.error(f"Gemini API error: {e}")
        return f"{title}، حبيبي، انشغلت ما اقدر ارد عليك، خلينا نكمل المحادثة بكرة إن شاء الله."

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    
    if len(user_message) > 5000:
        reply = f"{get_random_title()}, رسالتك طويلة جداً. اختصرها شوي"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    if not user:
        logger.error(f"Failed to get/create user: {user_id}")
        return
    
    bot_name = user['bot_name'] or 'وتين'
    step = user['step']
    
    if user_message.lower() in ["مساعدة", "help", "/help", "/start", "بداية"]:
        reply = f"{get_random_title()}، أهلاً!\nوش تحب تسميني؟ اختار لي اسم يعجبك"
        update_user(user_id, step=2)
    elif step == 2:
        chosen_name = user_message.strip()[:50]
        if len(chosen_name) < 2:
            reply = f"{get_random_title()}, اختار اسم أطول شوي"
        else:
            update_user(user_id, bot_name=chosen_name, step=3)
            reply = f"{get_random_title()}، تمام! من اليوم أنا {chosen_name}\nكيف حالك اليوم؟"
    else:
        reply = generate_ai_response(user_message, bot_name, user_id)
        save_conversation(user_id, user_message, reply)
        update_user(user_id)
    
    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    except LineBotApiError as e:
        logger.error(f"LINE API error: {e}")

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        logger.warning("Missing X-Line-Signature header")
        abort(400)
    
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Error in callback: {e}", exc_info=True)
        abort(500)
    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return """
    <html>
        <head><title>LoveBot</title></head>
        <body style='font-family: Arial; text-align: center; padding: 50px;'>
            <h1> LoveBot is Running!</h1>
            <p>Your emotional support companion is ready.</p>
        </body>
    </html>
    """, 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200

@app.errorhandler(404)
def not_found(error):
    return {"error": "Not found"}, 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return {"error": "Internal server error"}, 500

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 10000))
    debug = os.getenv("DEBUG", "False").lower() == "true"
    logger.info(f"🚀 Starting LoveBot on port {port}...")
    logger.info(f"📝 Debug mode: {debug}")
    app.run(host="0.0.0.0", port=port, debug=debug)
