import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai

# تحميل المتغيرات البيئية
load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# إعداد Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")

generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1000,
}

DB_PATH = "users.db"

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

# إنشاء جدول المستخدمين
def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                nickname TEXT,
                last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

init_db()

# جلب المستخدم
def get_user(user_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return c.fetchone()

# إنشاء مستخدم جديد
def create_user(user_id, nickname):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (user_id, nickname) VALUES (?, ?)",
            (user_id, nickname)
        )

# تحديث آخر تفاعل
def update_last_interaction(user_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE users SET last_interaction=? WHERE user_id=?",
            (datetime.now(), user_id)
        )

# توليد رد AI
def generate_ai_reply(user_text, nickname="حبيبي"):
    prompt = f"""
أنت حبيبة ودودة، تتكلم بعامية سعودية، مختصرة وعاطفية، بدون أي إيموجي.
المستخدم ({nickname}) قال: "{user_text}"
رد مختصر جداً (سطر أو سطرين) حنون وعاطفي.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except:
        return f"{nickname}، حصل خطأ حاول مرة ثانية"

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
        print(f"Error in callback: {e}")
        return "Internal error", 500
    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_text = event.message.text.strip()
    
    if not user_text:
        return

    # أمر التشغيل لاختبار البوت
    if user_text.lower() in ["تشغيل", "/test", "/ping"]:
        try:
            _ = generate_ai_reply("هل AI يعمل؟")
            reply = "تم تشغيل البوت بنجاح"
        except:
            reply = "حدث خطأ أثناء تشغيل البوت"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # جلب المستخدم
    user = get_user(user_id)
    if user is None:
        # مستخدم جديد - نسأل عن اسمه
        create_user(user_id, user_text[:50])
        reply = f"تشرفنا {user_text}، حبيبي\nكيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    nickname = user['nickname']
    # تحديث آخر تفاعل
    update_last_interaction(user_id)
    
    # توليد رد AI
    ai_reply = generate_ai_reply(user_text, nickname)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

@app.route("/", methods=["GET"])
def home():
    return "LINE LoveBot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE LoveBot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
