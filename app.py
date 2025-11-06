import os
import sqlite3
from flask import Flask, request, g
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime
import re
from contextlib import closing

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

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")

generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1000,
}

DATABASE = "users.db"
user_id_to_name = {}

# --- قاعدة البيانات ---
def init_db():
    with closing(sqlite3.connect(DATABASE)) as conn:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT,
                    last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
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

def refresh_user_names():
    global user_id_to_name
    with closing(sqlite3.connect(DATABASE)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, nickname FROM users WHERE nickname IS NOT NULL")
        user_id_to_name = {row[0]: row[1] for row in cursor.fetchall()}

# --- دالة توليد الردود AI ---
def generate_ai_reply(user_text, nickname, retries=2):
    prompt = f"""
أنت صديقة ودودة وعاطفية باللهجة السعودية. اجعل ردك مختصر (سطرين أو ثلاثة)، حقيقي، وداعم، بدون إيموجي.
المستخدم ({nickname}) قال: "{user_text}"
رد فقط النص بدون أي رموز أو مقدمات.
"""
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt, generation_config=generation_config)
            reply = response.text.strip()
            if not reply:
                continue
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
            print(f"Gemini API Error attempt {attempt+1}: {e}")
    return None  # إذا فشل، يرجع None ويتجاهل

# --- دالة البث الجماعي عبر AI ---
def broadcast_ai_message(base_message="وحشتني"):
    refresh_user_names()
    if not user_id_to_name:
        print("لا يوجد مستخدمين مسجلين")
        return
    
    success_count = 0
    fail_count = 0

    for user_id, nickname in user_id_to_name.items():
        ai_text = generate_ai_reply(base_message, nickname)
        if not ai_text:  # إذا فشل Gemini، تجاهل الإرسال
            fail_count += 1
            continue
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=ai_text))
            success_count += 1
        except Exception as e:
            print(f"خطأ بالإرسال إلى {nickname} ({user_id}): {e}")
            fail_count += 1

    print(f"\n=== نتيجة البث الجماعي ===\nنجح: {success_count} | فشل: {fail_count}")

# --- مسار callback ---
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
    cursor = db.cursor()
    cursor.execute("SELECT nickname FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    # أمر البداية / المساعدة
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        if not row:
            ai_reply = "مرحباً! أنا صديقتك الجديدة، وش تحب أناديك؟"
        else:
            nickname = row['nickname']
            ai_reply = f"أهلاً {nickname}! احكيلي عن يومك، مشاعرك، أو أي شيء تحب تشاركه."
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم جديد
    if not row:
        nickname = user_text
        cursor.execute(
            "INSERT INTO users (user_id, nickname, last_interaction) VALUES (?,?,?)",
            (user_id, nickname, datetime.now())
        )
        db.commit()
        user_id_to_name[user_id] = nickname
        ai_reply = generate_ai_reply("مرحباً", nickname)  # الرد الأول عبر AI
        if ai_reply:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم موجود
    nickname = row['nickname']
    user_id_to_name[user_id] = nickname
    ai_reply = generate_ai_reply(user_text, nickname)
    if ai_reply:
        cursor.execute(
            "UPDATE users SET last_interaction=? WHERE user_id=?",
            (datetime.now(), user_id)
        )
        db.commit()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
    # إذا فشل AI، يتجاهل ولا يرسل أي رد

# --- مسارات مساعدة ---
@app.route("/", methods=["GET"])
def home():
    return "LINE AI Friend Bot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    refresh_user_names()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "registered_users": len(user_id_to_name)
    }, 200

@app.route("/broadcast", methods=["POST"])
def broadcast_endpoint():
    """
    نقطة نهاية لإرسال البث الجماعي عبر AI
    """
    broadcast_ai_message("وحشتني")
    return {
        "status": "success",
        "message": "تم إرسال الرسالة لجميع المستخدمين عبر AI",
        "recipients": len(user_id_to_name)
    }, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE AI Friend Bot on port {port}...")
    refresh_user_names()
    print(f"تم تحميل {len(user_id_to_name)} مستخدم من قاعدة البيانات")
    app.run(host="0.0.0.0", port=port, debug=False)
