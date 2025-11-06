import os
import sqlite3
import threading
import random
from datetime import datetime, timedelta
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, QuickReply, QuickReplyButton, MessageAction
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

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")

generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1000,
}

# إعداد قاعدة البيانات
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    nickname TEXT,
    last_interaction DATETIME
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS user_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    message TEXT,
    bot_reply TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# مواقف يومية قصيرة
daily_scenarios = [
    "كان في موقف اليوم أثر فيك، تحب تحكين لي عنه؟",
    "مر يوم طويل، وش شعورك الحين؟",
    "حصل موقف غريب أو مضحك اليوم، تحب تشاركني؟",
    "اليوم شعرت بطاقة منخفضة، وش سويت لتحسن مزاجك؟",
    "قابلت أحد اليوم أو صار موقف مهم، وش صار؟"
]

# تذكير المستخدمين الذين لم يردوا
def daily_reminder():
    threading.Timer(3600, daily_reminder).start()  # كل ساعة للتذكير
    now = datetime.now()
    c.execute("SELECT user_id, last_interaction FROM users")
    users = c.fetchall()
    for user_id, last_interaction in users:
        if last_interaction:
            last_time = datetime.strptime(last_interaction, "%Y-%m-%d %H:%M:%S.%f")
            if now - last_time > timedelta(hours=6):  # إذا لم يتفاعل 6 ساعات
                try:
                    line_bot_api.push_message(user_id, TextSendMessage(text="حبيبي وينك؟ اشتقت لك"))
                except Exception as e:
                    print(f"Error sending reminder: {e}")

# بدء التذكير التلقائي
daily_reminder()

def generate_ai_reply(user_text, user_id):
    prompt = f"""
أنت صديقة ودودة وعاطفية سعودية، ترد على المستخدم بأسلوب مختصر ولطيف وكأنك تحبه.
ركز على شعوره، قدم نصائح عملية وعاطفية، واقترح طريقة صغيرة لتعزيز ثقته بنفسه.
أجب مباشرة دون مقدمات. المستخدم كتب:
{user_text}
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "حبيبي، ما فهمت قصدك، ممكن توضح لي شوي؟"

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

    # إضافة المستخدم إذا جديد
    c.execute("INSERT OR IGNORE INTO users (user_id, last_interaction) VALUES (?, ?)", (user_id, datetime.now()))
    conn.commit()

    # تحديث آخر تفاعل
    c.execute("UPDATE users SET last_interaction=? WHERE user_id=?", (datetime.now(), user_id))
    conn.commit()

    # أوامر المساعدة / البداية
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        ai_reply = "لبيه، أنا صديقتك الجديدة. اختار لي اسم تحبه."
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # الرد على اسم المستخدم إذا لم يكن معرف
    c.execute("SELECT nickname FROM users WHERE user_id=?", (user_id,))
    nickname = c.fetchone()[0]
    if not nickname:
        c.execute("UPDATE users SET nickname=? WHERE user_id=?", (user_text, user_id))
        conn.commit()
        reply = f"تمام حبيبي، أحبك أسميك {user_text}. كيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # إرسال رد AI عاطفي مختصر
    ai_reply = generate_ai_reply(user_text, user_id)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

    # تسجيل المحادثة
    c.execute("INSERT INTO user_logs (user_id, message, bot_reply) VALUES (?, ?, ?)",
              (user_id, user_text, ai_reply))
    conn.commit()

@app.route("/", methods=["GET"])
def home():
    return "LINE LovingBot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE LovingBot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
