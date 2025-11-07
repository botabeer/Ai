import os
import sqlite3
import threading
import time
import random
from datetime import datetime, timedelta
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from dotenv import load_dotenv
import google.generativeai as genai
from contextlib import contextmanager

# تحميل المتغيرات البيئية
load_dotenv()

app = Flask(__name__)

# إعداد المتغيرات
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

# LINE Bot
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")
generation_config = {
    "temperature": 0.8,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1500,
}

# قاعدة البيانات
DB_PATH = "lovebot.db"
db_lock = threading.Lock()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            bot_name TEXT,
            user_nickname TEXT,
            last_interaction TEXT,
            step INTEGER DEFAULT 1,
            auto_message_count INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_msg TEXT,
            bot_reply TEXT,
            timestamp TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )''')
        conn.commit()

def get_user(user_id):
    with db_lock:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            return c.fetchone()

def create_user(user_id):
    now = datetime.now().isoformat()
    with db_lock:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO users (user_id, last_interaction, step, auto_message_count) VALUES (?, ?, 1, 0)",
                (user_id, now)
            )
            conn.commit()

def update_user(user_id, bot_name=None, user_nickname=None, step=None):
    now = datetime.now().isoformat()
    with db_lock:
        with get_db() as conn:
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

def save_conversation(user_id, user_msg, bot_reply):
    now = datetime.now().isoformat()
    with db_lock:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO conversations (user_id, user_msg, bot_reply, timestamp) VALUES (?, ?, ?, ?)",
                (user_id, user_msg, bot_reply, now)
            )
            conn.commit()

def get_idle_users(max_idle_hours=2):
    cutoff_time = (datetime.now() - timedelta(hours=max_idle_hours)).isoformat()
    with db_lock:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT user_id, bot_name, user_nickname, auto_message_count
                FROM users
                WHERE last_interaction < ?
                AND step >= 4
                AND auto_message_count < 3""",
                (cutoff_time,)
            )
            return c.fetchall()

def increment_auto_message_count(user_id):
    with db_lock:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE users SET auto_message_count = auto_message_count + 1 WHERE user_id=?",
                (user_id,)
            )
            conn.commit()

def reset_auto_message_count(user_id):
    with db_lock:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE users SET auto_message_count = 0 WHERE user_id=?",
                (user_id,)
            )
            conn.commit()

def get_bot_personality_prompt(bot_name="وتين", user_nickname=None):
    nickname_context = "تناديه حبيبي أو بأسماء دلع" if not user_nickname else f"تناديه {user_nickname}"
    return f"""أنت {bot_name}، بنت سعودية ودودة وحنونة، مختصرة في الردود.
تتكلمين بعفوية، جمل قصيرة، باللهجة الخليجية السعودية، بدون إيموجي.
{nickname_context} بشكل دافئ، تعطي حلول غير مباشرة لمشاكل العلاقات، تساعد على تطوير الذات والثقة بالنفس، بدون نصائح مباشرة."""

USER_LIQUID_NAMES = ["حبيبي", "قلبي", "يا روحي", "دنيتي", "يا بعد عمري"]

def choose_user_nickname():
    return random.choice(USER_LIQUID_NAMES)

def get_auto_messages(bot_name=None, user_nickname=None):
    nickname = user_nickname if user_nickname else choose_user_nickname()
    name_suffix = f"\n- {bot_name}" if bot_name else ""
    messages = [
        f"{nickname}، وينك؟ اشتقت لك{name_suffix}",
        f"{nickname}، أفكر فيك الحين{name_suffix}",
        f"{nickname}، كيف يومك؟ اتمنى تكون بخير{name_suffix}"
    ]
    return messages

CHECK_INTERVAL = 300
MAX_IDLE_HOURS = 2

def send_auto_messages():
    while True:
        try:
            idle_users = get_idle_users(MAX_IDLE_HOURS)
            for user_id, bot_name, user_nickname, auto_count in idle_users:
                try:
                    messages = get_auto_messages(bot_name, user_nickname)
                    message_index = min(auto_count, len(messages)-1)
                    message = messages[message_index]
                    line_bot_api.push_message(user_id, TextSendMessage(text=message))
                    increment_auto_message_count(user_id)
                    time.sleep(1)
                except Exception as e:
                    print(f"Failed auto message to {user_id}: {e}")
        except Exception as e:
            print(f"Auto-message loop error: {e}")
        time.sleep(CHECK_INTERVAL)

def generate_ai_response(user_message, bot_name=None, user_nickname=None):
    personality = get_bot_personality_prompt(bot_name or "وتين", user_nickname)
    prompt = f"""{personality}

رسالة المستخدم:
{user_message}

## قواعد الرد:
- ركز على تطوير الذات والثقة بالنفس
- ساعد في تجاوز الصعوبات العاطفية بطريقة غير مباشرة
- استخدم جمل قصيرة ومباشرة، 1-2 جملة
- ودية، عفوية، بدون إيموجي
- اعط أمثلة أو حلول واقعية بدون إعطاء أوامر مباشرة

ردك:"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        ai_reply = response.text.strip()
        if not ai_reply:
            return "مافهمتك، وضح لي أكثر"
        return ai_reply[:4900]
    except Exception as e:
        print(f"Gemini API error: {e}")
        return "معذرة، صار عندي خطأ بسيط. حاول مرة ثانية"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    try:
        user = get_user(user_id)
        if not user:
            create_user(user_id)
            user = get_user(user_id)

        user_id_db, bot_name, user_nickname, last_interaction, step, auto_count = user

        if auto_count > 0:
            reset_auto_message_count(user_id)

        # أمر مساعدة
        if user_message.lower() in ["مساعدة", "help"]:
            nickname = choose_user_nickname()
            update_user(user_id, user_nickname=nickname, step=4)
            reply = f"{nickname}، وش تحب تسميني؟ اختار لي اسم يعجبك"
        elif step == 1:
            reply = "أهلاً أنا بوت\nوش تحب تسميني؟ اختار لي اسم يعجبك"
            update_user(user_id, step=2)
        elif step == 2:
            chosen_name = user_message.strip()
            update_user(user_id, bot_name=chosen_name, step=3)
            reply = f"تمام! من اليوم أنا {chosen_name}\nوش تحب أناديك؟"
        elif step == 3:
            chosen_nickname = user_message.strip()
            update_user(user_id, user_nickname=chosen_nickname, step=4)
            reply = f"{chosen_nickname}، كيف حالك اليوم؟"
        else:
            reply = generate_ai_response(user_message, bot_name, user_nickname)
            save_conversation(user_id, user_message, reply)
            update_user(user_id)

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    except Exception as e:
        print(f"Error handling message: {e}")
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="معذرة، صار خطأ. جرب مرة ثانية"))
        except:
            pass

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    except Exception as e:
        print(f"Error in callback: {e}")
        return "Internal error", 500
    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return "🤖 LoveBot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM users")
            user_count = c.fetchone()[0]
        return {"status": "healthy", "users": user_count}, 200
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

if __name__ == "__main__":
    init_db()
    threading.Thread(target=send_auto_messages, daemon=True).start()
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Starting LoveBot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
