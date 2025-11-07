import os
import sqlite3
from datetime import datetime
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
import random

# تحميل المتغيرات البيئية
load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

# LINE Bot
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini AI
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
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    bot_name TEXT,
    user_nickname TEXT,
    last_interaction TEXT,
    step INTEGER DEFAULT 1
)''')
c.execute('''CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_msg TEXT,
    bot_reply TEXT,
    timestamp TEXT
)''')
conn.commit()

def get_user(user_id):
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return c.fetchone()

def create_user(user_id):
    now = datetime.now().isoformat()
    c.execute("INSERT OR IGNORE INTO users (user_id, last_interaction, step) VALUES (?, ?, 1)",
              (user_id, now))
    conn.commit()

def update_user(user_id, bot_name=None, user_nickname=None, step=None):
    now = datetime.now().isoformat()
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
    c.execute("INSERT INTO conversations (user_id, user_msg, bot_reply, timestamp) VALUES (?, ?, ?, ?)",
              (user_id, user_msg, bot_reply, now))
    conn.commit()

# ألقاب المستخدم
USER_TITLES = ["حبيبي", "قلبي", "يا روحي", "يا بعد عمري", "دنيتي", "عمري"]

def get_random_title():
    return random.choice(USER_TITLES)

def generate_ai_response(user_message, bot_name="وتين"):
    """توليد ردود ذكية قصيرة بأسلوب ودود وحنون"""
    title = get_random_title()
    personality_prompt = f"""
أنت {bot_name}، بنت خليجية سعودية ودودة وحنونة، تتحدث بأسلوب مباشر وبسيط، مختصر، بدون إيموجي، 
تحب مخاطبة المستخدم دائمًا بألقاب مثل "حبيبي"، "قلبي"، "يا روحي"، بدون استخدام اسمه.
تركز على الدعم النفسي، تطوير الشخصية، تعزيز الثقة بالنفس، وتقديم حلول مناسبة للعلاقات العاطفية بطريقة غير مباشرة.
"""
    prompt = f"""{personality_prompt}

## رسالة المستخدم:
{user_message}

## ردك (قصير، ودود، ذكي، 1-2 جملة، مخاطبة المستخدم باللقب):
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        ai_reply = response.text.strip()
        if not ai_reply:
            return f"{title}, ما فهمتك وضح لي أكثر"
        return ai_reply[:4900]
    except Exception:
        return f"{title}, معذرة، صار عندي خطأ بسيط. حاول مرة ثانية"

# معالجة الرسائل
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    user_id_db, bot_name, user_nickname, last_interaction, step = user

    # أمر المساعدة يبدأ المحادثة
    if user_message.lower() in ["مساعدة", "help", "/help", "/start"]:
        reply = f"{get_random_title()}, أهلاً أنا بوت\nوش تحب تسميني؟ اختار لي اسم يعجبك"
        update_user(user_id, step=2)
    elif step == 2:
        chosen_name = user_message.strip()
        update_user(user_id, bot_name=chosen_name, step=3)
        reply = f"{get_random_title()}, تمام! من اليوم أنا {chosen_name}. وش مسوي اليوم؟"
    else:
        reply = generate_ai_response(user_message, bot_name)
        save_conversation(user_id, user_message, reply)
        update_user(user_id)

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

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
    return "LoveBot is running!", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Starting LoveBot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
