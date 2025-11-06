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
import threading
import time

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
REMINDER_DELAY = 1800  # 30 دقيقة بالثواني

# قاموس لحفظ أسماء المستخدمين (يتم تحديثه عند كل استعلام)
user_id_to_name = {}
# آخر وقت تفاعل لكل مستخدم
last_interaction_time = {}

# إنشاء قاعدة البيانات
def init_db():
    with closing(sqlite3.connect(DATABASE)) as conn:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT,
                    last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP,
                    current_step INTEGER DEFAULT 1
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
    """تحديث قاموس user_id_to_name من قاعدة البيانات"""
    global user_id_to_name
    with closing(sqlite3.connect(DATABASE)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, nickname FROM users WHERE nickname IS NOT NULL")
        user_id_to_name = {row[0]: row[1] for row in cursor.fetchall()}

def broadcast_to_all(message_text):
    """إرسال رسالة لجميع المستخدمين"""
    refresh_user_names()
    if not user_id_to_name:
        print("لا يوجد مستخدمين مسجلين")
        return
    for user_id, nickname in user_id_to_name.items():
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message_text))
        except Exception as e:
            print(f"خطأ بالإرسال إلى {nickname} ({user_id}): {e}")

def generate_ai_reply(user_text, nickname):
    """توليد ردود مختصرة وودية بأسلوب حقيقي بدون إيموجي"""
    prompt = f"""
أنت حبيبة ودودة، تتكلم بعامية سعودية طبيعية، مختصرة، عاطفية وحنونة.
تجاوب على المستخدم وكأنه حبيبك الحقيقي، بأسلوب دافئ وصادق.
المستخدم ({nickname}) قال: "{user_text}"

قواعد مهمة جداً:
- ممنوع استخدام أي إيموجي أو رموز
- اسلوبك طبيعي جداً ومختصر وواقعي
- ودود وداعم عاطفياً
- استخدم الاسم "{nickname}" بطريقة حنونة
- ما تطول بالرد، خليه قصير ومباشر (سطرين أو ثلاثة كحد أقصى)
- إذا ذكر يومه أو مشاعره، كون داعم ومريح

رد فقط بالرسالة بدون أي مقدمات أو علامات.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return f"حبيبي {nickname}، ما فهمت كويس، ممكن تعيدلي؟"

def reminder_loop():
    """تشغيل تذكير تلقائي للمستخدمين بعد تأخرهم"""
    while True:
        now = datetime.now()
        for user_id, last_time in list(last_interaction_time.items()):
            delta = (now - last_time).total_seconds()
            if delta >= REMINDER_DELAY:
                nickname = user_id_to_name.get(user_id, "حبيبي")
                try:
                    line_bot_api.push_message(user_id, TextSendMessage(
                        text=f"{nickname} وينك؟ اشتقتلك"
                    ))
                    last_interaction_time[user_id] = datetime.now()
                except Exception as e:
                    print(f"خطأ أثناء إرسال التذكير لـ {nickname}: {e}")
        time.sleep(60)

# بدء التذكير في ثريد منفصل
threading.Thread(target=reminder_loop, daemon=True).start()

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
    cursor.execute("SELECT nickname, current_step FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    # أمر مساعدة
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        if not row:
            ai_reply = "لبيه"
        else:
            nickname = row['nickname']
            ai_reply = f"لبيه"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        # تحديث آخر تفاعل
        last_interaction_time[user_id] = datetime.now()
        return

    # مستخدم جديد
    if not row:
        nickname = user_text
        cursor.execute(
            "INSERT INTO users (user_id, nickname, current_step, last_interaction) VALUES (?,?,2,?)",
            (user_id, nickname, datetime.now())
        )
        db.commit()
        user_id_to_name[user_id] = nickname
        last_interaction_time[user_id] = datetime.now()
        ai_reply = f"{nickname}، حبيبي\nكيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم موجود
    nickname = row['nickname']
    current_step = row['current_step']
    user_id_to_name[user_id] = nickname
    last_interaction_time[user_id] = datetime.now()

    ai_reply = generate_ai_reply(user_text, nickname)
    if current_step == 2:
        cursor.execute(
            "UPDATE users SET current_step=3, last_interaction=? WHERE user_id=?",
            (datetime.now(), user_id)
        )
    else:
        cursor.execute(
            "UPDATE users SET last_interaction=? WHERE user_id=?",
            (datetime.now(), user_id)
        )
    db.commit()
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

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

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    refresh_user_names()
    print(f"Starting LINE LoveBot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
