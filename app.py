import os
from datetime import datetime
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import sqlite3
from contextlib import closing
import google.generativeai as genai
import logging
import re

# تحميل المتغيرات البيئية
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# إعداد Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")
generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 300,
}

DB_PATH = "users.db"

# إنشاء قاعدة البيانات والجداول
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT,
                    registration_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    message TEXT,
                    response TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
init_db()

# دوال قاعدة البيانات
def get_user(user_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return c.fetchone()

def create_user(user_id, nickname):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id, nickname) VALUES (?, ?)", (user_id, nickname))
        conn.commit()

def update_user_interaction(user_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET last_interaction=? WHERE user_id=?", (datetime.now(), user_id))
        conn.commit()

def save_conversation(user_id, message, response):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO conversations (user_id, message, response) VALUES (?, ?, ?)",
                  (user_id, message, response))
        conn.commit()

# تحليل المشاعر باستخدام كلمات مفتاحية بسيطة
def detect_mood(user_text):
    positive_words = ["حلو", "جميل", "رائع", "ممتاز", "سعيد", "فرحان", "مبسوط", "تمام", "كويس"]
    negative_words = ["تعبان", "زعلان", "حزين", "صعب", "متضايق", "مو حلو", "زفت", "سيء"]
    text_lower = user_text.lower()
    if any(word in text_lower for word in positive_words):
        return "positive"
    elif any(word in text_lower for word in negative_words):
        return "negative"
    else:
        return "neutral"

# توليد رد AI حسب المزاج
def generate_ai_reply(nickname, user_text):
    mood = detect_mood(user_text)
    if mood == "positive":
        mood_hint = "كن داعم وسعيد، أظهر فرحة واهتمام"
    elif mood == "negative":
        mood_hint = "كن مواسياً وداعماً، أظهر تعاطف وحب"
    else:
        mood_hint = "كن ودوداً وحنوناً"

    prompt = f"""
أنت شخصية ودودة ومحبة، تتحدث بعامية سعودية طبيعية.
{mood_hint}.
أجب على المستخدم باسمه "{nickname}" بطريقة مختصرة ودافئة (سطرين أو ثلاثة كحد أقصى)،
بدون أي رموز أو إيموجي.
المستخدم قال: "{user_text}"
رد فقط بالنص بدون أي مقدمات.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        reply = response.text.strip()
        # إزالة أي رموز أو إيموجي متبقية
        emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"
            u"\U0001F300-\U0001F5FF"
            u"\U0001F680-\U0001F6FF"
            u"\U0001F1E0-\U0001F1FF"
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        reply = emoji_pattern.sub(r'', reply).strip()
        return reply
    except Exception as e:
        logger.error(f"Gemini API Error: {e}")
        return f"{nickname} حبيبي، ما فهمت كويس، ممكن تعيدلي؟"

# التعامل مع رسائل LINE
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        user_id = event.source.user_id
        user_text = event.message.text.strip()
        if not user_text:
            return

        user = get_user(user_id)

        # مستخدم جديد لم يسجل الاسم بعد
        if not user:
            reply = "هلا حبيبي، وش اسمك؟"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        nickname = user['nickname']

        # إذا الاسم لم يُسجل (فارغ) و المستخدم كتب اسمه
        if not nickname:
            nickname = user_text[:50]
            create_user(user_id, nickname)
            reply = f"تشرفنا {nickname} حبيبي\nكيف كان يومك اليوم؟"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        # الرد باستخدام Gemini AI مع تحليل المشاعر
        ai_reply = generate_ai_reply(nickname, user_text)

        # حفظ المحادثة وتحديث آخر تفاعل
        save_conversation(user_id, user_text, ai_reply)
        update_user_interaction(user_id)

        # إرسال الرد
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="عذراً حبيبي صار خطأ\nجرب مرة ثانية"))
        except:
            pass

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        return "Missing signature", 400
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    except Exception as e:
        logger.error(f"Error in callback: {e}")
        return "Internal error", 500
    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return "LINE LoveBot with Gemini AI & Mood Analysis is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    logger.info(f"Starting LINE LoveBot on port {port}…")
    app.run(host="0.0.0.0", port=port, debug=False)
