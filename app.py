import os
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import LineBotApiError, InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime
import sqlite3
from contextlib import closing

# تحميل المتغيرات البيئية
load_dotenv()

app = Flask(__name__)

# إعداد المتغيرات
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

# LINE Bot API (v2)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")
generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1000,
}

# قاعدة البيانات
DB_PATH = "users.db"

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                nickname TEXT,
                last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

init_db()

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

def update_last_interaction(user_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET last_interaction=? WHERE user_id=?", (datetime.now(), user_id))
        conn.commit()

def generate_ai_reply(nickname, user_text):
    prompt = f"""
أنت حبيبة ودودة، تتكلم بعامية سعودية مختصرة وحنونة.  
المستخدم ({nickname}) قال: "{user_text}"  

الرد: مختصر، حنوني، سطرين أو ثلاثة فقط، بدون أي إيموجي أو رموز.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return f"{nickname}، حبيبي، ما فهمت كويس، ممكن تعيدلي؟"

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
        print(f"Webhook error: {e}")
        return "Internal error", 500
    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    if not user_text:
        return

    user = get_user(user_id)

    if user is None:
        # مستخدم جديد يختار اسمه
        nickname = user_text[:50]
        create_user(user_id, nickname)
        reply_text = f"تشرفنا {nickname} حبيبي\nكيف كان يومك اليوم؟"
    else:
        nickname = user["nickname"]
        reply_text = generate_ai_reply(nickname, user_text)
        update_last_interaction(user_id)

    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    except LineBotApiError as e:
        print(f"LINE API Error: {e}")

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
