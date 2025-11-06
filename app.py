import os
import sqlite3
from flask import Flask, request, g
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime

load_dotenv()
app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")

generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1000,
}

DATABASE = "users.db"

# إنشاء قاعدة البيانات لتخزين أسماء المستخدمين وحالة الحوار
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            nickname TEXT,
            last_step INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def generate_ai_reply(user_text, nickname):
    prompt = f"""
أنت صديقة ودودة، عاطفية وحنونة، تتكلم بالعربية العامية السعودية.
مختصرة جداً، سطرين أو ثلاثة، بدون إيموجي أو رموز.
تجاوب على {nickname} كأنه حبيبك، بطريقة صادقة وداعمة.

المستخدم ({nickname}) قال: "{user_text}"

رد فقط بالرسالة، بدون مقدمات.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return f"{nickname}، لم أفهم كويس، ممكن توضح لي؟"

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

    db = get_db()
    c = db.cursor()
    c.execute("SELECT nickname, last_step FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()

    # مستخدم جديد: نحفظ اسمه
    if not row:
        nickname = user_text
        c.execute(
            "INSERT INTO users (user_id, nickname, last_step) VALUES (?, ?, ?)",
            (user_id, nickname, 2)
        )
        db.commit()
        reply_text = f"{nickname}، حبيبي. كيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # مستخدم موجود
    nickname = row['nickname']
    reply_text = generate_ai_reply(user_text, nickname)
    c.execute("UPDATE users SET last_step=2 WHERE user_id=?", (user_id,))
    db.commit()

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

@app.route("/", methods=["GET"])
def home():
    return "LINE AI LoveBot is running!", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
