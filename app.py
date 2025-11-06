import os
import sqlite3
from flask import Flask, request, g
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime
from contextlib import closing
import re

# --- تحميل المتغيرات البيئية ---
load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

# --- إعداد LINE Bot و Gemini ---
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

# --- قاعدة البيانات ---
DATABASE = "users.db"
user_id_to_name = {}

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

# --- دالة توليد الردود مع retry لتجنب fallback المتكرر ---
def generate_ai_reply(user_text, nickname, retries=3):
    prompt = f"""
أنت حبيبة ودودة، تتكلم بعامية سعودية طبيعية، مختصرة، عاطفية وحنونة.
تجاوب على المستخدم وكأنه حبيبك الحقيقي، بأسلوب دافئ وصادق.
المستخدم ({nickname}) قال: "{user_text}"

قواعد مهمة جداً:
- ممنوع استخدام أي إيموجي نهائياً
- اكتب نص عادي فقط
- اسلوبك طبيعي جداً ومختصر وواقعي
- ودود وداعم عاطفياً
- استخدم الاسم "{nickname}" بطريقة حنونة
- ما تطول بالرد، خليه قصير ومباشر (سطرين أو ثلاثة كحد أقصى)
- إذا ذكر يومه أو مشاعره، كون داعم ومريح

رد فقط بالرسالة بدون أي مقدمات أو علامات أو رموز.
"""
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt, generation_config=generation_config)
            reply = response.text.strip()
            if reply:
                # إزالة أي إيموجي أو رموز قد تظهر
                emoji_pattern = re.compile("["
                    u"\U0001F600-\U0001F64F"
                    u"\U0001F300-\U0001F5FF"
                    u"\U0001F680-\U0001F6FF"
                    u"\U0001F1E0-\U0001F1FF"
                    u"\U00002702-\U000027B0"
                    u"\U000024C2-\U0001F251"
                    "]+", flags=re.UNICODE)
                return emoji_pattern.sub(r'', reply).strip()
        except Exception as e:
            print(f"Gemini API Error on attempt {attempt+1}: {e}")
    return f"حبيبي {nickname}، ما فهمت كويس، ممكن تعيدلي؟"

# --- دالة البث الجماعي ---
def broadcast_to_all():
    refresh_user_names()
    if not user_id_to_name:
        print("لا يوجد مستخدمين مسجلين")
        return
    
    message_text = "حبيبي وينك؟ وحشتني"
    success_count = 0
    fail_count = 0

    for user_id, nickname in user_id_to_name.items():
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message_text))
            success_count += 1
        except Exception as e:
            print(f"خطأ بالإرسال إلى {nickname}: {e}")
            fail_count += 1

    print(f"=== نتيجة البث ===\nنجح: {success_count} | فشل: {fail_count}")

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

    # أمر المساعدة
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        if row:
            nickname = row['nickname']
            ai_reply = f"أهلاً {nickname}، احكيلي أي شيء تحب أسمعه"
        else:
            ai_reply = "مرحباً، وش أحب أناديك؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم جديد
    if not row:
        nickname = user_text
        cursor.execute(
            "INSERT INTO users (user_id, nickname) VALUES (?,?)",
            (user_id, nickname)
        )
        db.commit()
        ai_reply = f"{nickname}، حبيبي\nكيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم موجود
    nickname = row['nickname']
    ai_reply = generate_ai_reply(user_text, nickname)
    cursor.execute(
        "UPDATE users SET last_interaction=CURRENT_TIMESTAMP WHERE user_id=?",
        (user_id,)
    )
    db.commit()
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

# --- مسارات مساعدة ---
@app.route("/", methods=["GET"])
def home():
    return "LINE LoveBot is running!", 200

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
    broadcast_to_all()
    return {
        "status": "success",
        "message": "تم إرسال الرسالة لجميع المستخدمين",
        "recipients": len(user_id_to_name)
    }, 200

# --- تشغيل البوت ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    refresh_user_names()
    print(f"تم تحميل {len(user_id_to_name)} مستخدم من قاعدة البيانات")
    app.run(host="0.0.0.0", port=port, debug=False)
