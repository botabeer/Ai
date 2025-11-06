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

# قاموس لحفظ أسماء المستخدمين (يتم تحديثه عند كل استعلام)
user_id_to_name = {}

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

# تحديث قاموس الأسماء من قاعدة البيانات
def refresh_user_names():
    """تحديث قاموس user_id_to_name من قاعدة البيانات"""
    global user_id_to_name
    with closing(sqlite3.connect(DATABASE)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, nickname FROM users WHERE nickname IS NOT NULL")
        user_id_to_name = {row[0]: row[1] for row in cursor.fetchall()}

def broadcast_to_all(message_text):
    """
    ترسل رسالة نصية لجميع المستخدمين المسجلين في user_id_to_name.
    يمكن استدعاؤها كـ broadcast_to_all("نص تجريبي")
    """
    refresh_user_names()  # تحديث القائمة قبل الإرسال
    
    if not user_id_to_name:
        print("لا يوجد مستخدمين مسجلين")
        return
    
    success_count = 0
    fail_count = 0
    
    for user_id, nickname in user_id_to_name.items():
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message_text))
            print(f"تم الإرسال إلى {nickname} ({user_id})")
            success_count += 1
        except Exception as e:
            print(f"خطأ بالإرسال إلى {nickname} ({user_id}): {e}")
            fail_count += 1
    
    print(f"\n=== نتيجة البث ===")
    print(f"نجح: {success_count} | فشل: {fail_count}")

def generate_ai_reply(user_text, nickname):
    """توليد ردود مختصرة وودية بأسلوب حقيقي بدون إيموجي"""
    prompt = f"""
أنت حبيبة ودودة، تتكلم بعامية سعودية طبيعية، مختصرة، عاطفية وحنونة.
تجاوب على المستخدم وكأنه حبيبك الحقيقي، بأسلوب دافئ وصادق.
المستخدم ({nickname}) قال: "{user_text}"

قواعد مهمة جداً:
- ممنوع استخدام أي إيموجي نهائياً
- ممنوع استخدام أي رموز تعبيرية
- اكتب نص عادي فقط
- اسلوبك طبيعي جداً ومختصر وواقعي
- ودود وداعم عاطفياً
- استخدم الاسم "{nickname}" بطريقة حنونة
- ما تطول بالرد، خليه قصير ومباشر (سطرين أو ثلاثة كحد أقصى)
- إذا ذكر يومه أو مشاعره، كون داعم ومريح
- استخدم كلمات حنونة وعاطفية بس بدون مبالغة

أمثلة على أسلوب الرد:
- "حبيبي، أنا هنا معاك، احكيلي أكثر"
- "يا قلبي، يومك صعب يبدو، بس تذكر إنك قوي"
- "والله إني فرحانة لك، تستاهل كل خير"

رد فقط بالرسالة بدون أي مقدمات أو علامات أو رموز.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        reply = response.text.strip()
        # إزالة أي إيموجي متبقي (احتياطي)
        import re
        # إزالة الإيموجي والرموز التعبيرية
        emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"  # emoticons
            u"\U0001F300-\U0001F5FF"  # symbols & pictographs
            u"\U0001F680-\U0001F6FF"  # transport & map symbols
            u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        reply = emoji_pattern.sub(r'', reply).strip()
        return reply
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return f"حبيبي {nickname}، ما فهمت كويس، ممكن تعيدلي؟"

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
            ai_reply = "مرحباً، أنا صديقتك الجديدة\nوش تحب أناديك؟"
        else:
            # مستخدم موجود
            nickname = row['nickname']
            ai_reply = f"أهلاً {nickname}\n\nأنا هنا عشان أسمعك وأكون معاك\nاحكيلي عن يومك، مشاعرك، أي شي تبي تشاركه"
        
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
        
        # تحديث القاموس
        user_id_to_name[user_id] = nickname
        
        ai_reply = f"{nickname}، حبيبي\nكيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم موجود
    nickname = row['nickname']
    current_step = row['current_step']
    
    # تحديث القاموس
    user_id_to_name[user_id] = nickname

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
    return "LINE LoveBot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    refresh_user_names()
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "registered_users": len(user_id_to_name)
    }, 200

# نقطة نهاية لإرسال رسالة جماعية (للاختبار فقط - احذفها في الإنتاج)
@app.route("/broadcast", methods=["POST"])
def broadcast_endpoint():
    """
    نقطة نهاية لإرسال رسالة جماعية
    استخدم: POST /broadcast مع body: {"message": "نص الرسالة"}
    """
    data = request.get_json()
    if not data or 'message' not in data:
        return {"error": "يجب إرسال 'message' في الـ body"}, 400
    
    message = data['message']
    broadcast_to_all(message)
    
    return {
        "status": "success",
        "message": "تم إرسال الرسالة لجميع المستخدمين",
        "recipients": len(user_id_to_name)
    }, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE LoveBot on port {port}...")
    # تحديث قائمة المستخدمين عند بدء التشغيل
    refresh_user_names()
    print(f"تم تحميل {len(user_id_to_name)} مستخدم من قاعدة البيانات")
    app.run(host="0.0.0.0", port=port, debug=False)
