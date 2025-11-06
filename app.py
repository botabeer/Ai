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

# الحصول على اتصال قاعدة البيانات لكل طلب
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

# إغلاق الاتصال بعد كل طلب
@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def generate_ai_reply(user_text, nickname):
    """توليد ردود مختصرة وودية بأسلوب حقيقي"""
    prompt = f"""
أنت حبيبة ودودة، تتكلم بعامية سعودية طبيعية، مختصرة، عاطفية وحنونة.
تجاوب على المستخدم وكأنه حبيبك الحقيقي، بأسلوب دافئ وصادق.
المستخدم ({nickname}) قال: "{user_text}"

اسلوبك:
- طبيعي جداً ومختصر وواقعي
- ودود وداعم عاطفياً
- استخدم الاسم "{nickname}" بطريقة حنونة
- ما تطول بالرد، خليه قصير ومباشر
- إذا ذكر يومه أو مشاعره، كون داعم ومريح
- ممكن تستخدم إيموجي بسيط لو مناسب (بس لا تكثر)

رد فقط بالرسالة بدون أي مقدمات.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return f"حبيبي {nickname}، ما فهمت كويس، ممكن تعيدلي؟ 💭"

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

    # البحث عن المستخدم في قاعدة البيانات
    cursor.execute("SELECT nickname, current_step FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    # أمر مساعدة
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        if not row:
            # مستخدم جديد
            ai_reply = "مرحباً! أنا صديقتك الجديدة \nوش تحب أناديك؟"
        else:
            # مستخدم موجود
            nickname = row['nickname']
            ai_reply = f"أهلاً {nickname}! \n\nأنا هنا عشان أسمعك وأكون معاك.\nاحكيلي عن يومك، مشاعرك، أي شي تبي تشاركه 💭"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم جديد
    if not row:
        # حفظ الاسم الذي اختاره المستخدم
        nickname = user_text
        cursor.execute(
            "INSERT INTO users (user_id, nickname, current_step, last_interaction) VALUES (?,?,2,?)",
            (user_id, nickname, datetime.now())
        )
        db.commit()
        ai_reply = f"{nickname}، حبيبي \nكيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم موجود
    nickname = row['nickname']
    current_step = row['current_step']

    # متابعة الحوار حسب الخطوة
    if current_step == 2:
        # المستخدم رد عن يومه، ننتقل للخطوة 3
        ai_reply = generate_ai_reply(user_text, nickname)
        cursor.execute(
            "UPDATE users SET current_step=3, last_interaction=? WHERE user_id=?",
            (datetime.now(), user_id)
        )
        db.commit()
    else:
        # الخطوة 3: الحوار مستمر
        ai_reply = generate_ai_reply(user_text, nickname)
        cursor.execute(
            "UPDATE users SET last_interaction=? WHERE user_id=?",
            (datetime.now(), user_id)
        )
        db.commit()
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

@app.route("/", methods=["GET"])
def home():
    return "LINE LoveBot is running! ", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE LoveBot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
