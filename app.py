import os
import sqlite3
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, MessageAction
)
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime

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
    "temperature": 0.8,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 2000,
}

# إعداد قاعدة البيانات
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    last_interaction DATETIME,
    progress_score INTEGER DEFAULT 0
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS user_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_message TEXT,
    bot_reply TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

def generate_daily_scenario(user_id):
    """
    توليد موقف يومي جديد باستخدام Gemini AI
    مع 3 خيارات، تحليل لكل خيار، والحل الأمثل
    """
    prompt = f"""
أنت مساعد ذكي لتطوير الذات والعلاقات الاجتماعية.
أعطِ موقف يومي قصير، واقعي، شامل لتقوية الشخصية، الكاريزما، اللباقة، والتحكم بالمشاعر.
الموقف يجب أن يكون عام، ويمكن أن يتعلق بالعلاقات العاطفية أو المهنية أو الاجتماعية.
اعطِ ثلاثة خيارات للتصرف بشكل لبق، مع تحليل لكل خيار وإشارة إلى الخيار الأفضل.
أعد الرد بصيغة JSON كما يلي:

{{
  "scenario": "... نص الموقف ...",
  "options": [
    {{"text": "... الخيار 1 ...", "analysis": "... تحليله ...", "is_correct": true/false}},
    {{"text": "... الخيار 2 ...", "analysis": "... تحليله ...", "is_correct": true/false}},
    {{"text": "... الخيار 3 ...", "analysis": "... تحليله ...", "is_correct": true/false}}
  ]
}}
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        import json
        # محاولة تحويل النص الناتج إلى JSON
        scenario_json = json.loads(response.text.strip())
        return scenario_json
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return None

def generate_ai_reply(user_id, user_text, context=""):
    """
    رد ذكي كما لو تتحدث مع إنسان، مع حفظ سجل لتجنب التكرار
    """
    c.execute("SELECT user_message, bot_reply FROM user_logs WHERE user_id=? ORDER BY timestamp DESC LIMIT 5", (user_id,))
    history = c.fetchall()
    history_text = "\n".join([f"المستخدم: {u}\nالبوت: {b}" for u,b in reversed(history)]) if history else ""

    prompt = f"""
أنت مساعد ذكي وطبيعي للغاية. تحدث مع المستخدم وكأنه إنسان حقيقي.
السياق السابق:
{history_text}

{context}

رسالة المستخدم الأخيرة: {user_text}

أجب بطريقة ودية، ذكية، واقعية، مع نصائح عملية عند الحاجة، دون تكرار الردود السابقة.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "عذرًا، لم أتمكن من الرد الآن، حاول مرة أخرى."

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

    # تسجيل المستخدم إذا جديد
    c.execute("INSERT OR IGNORE INTO users (user_id, last_interaction) VALUES (?, ?)", (user_id, datetime.now()))
    conn.commit()

    # أوامر المساعدة
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        ai_reply = (
            "مرحباً! 🌟\n"
            "يمكنك التحدث معي بحرية عن أي موضوع، أو تجربة مواقف يومية لتطوير الكاريزما واللباقة والثقة بالنفس.\n"
            "للبدء، اكتب: 'موقف اليوم'"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # طلب موقف اليوم
    if user_text.lower() == "موقف اليوم":
        scenario_obj = generate_daily_scenario(user_id)
        if scenario_obj is None:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="عذرًا، لم أتمكن من توليد الموقف اليوم، حاول لاحقًا."))
            return
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label=opt['text'], text=opt['text']))
            for opt in scenario_obj['options']
        ])
        reply = TextSendMessage(
            text=scenario_obj['scenario'],
            quick_reply=quick_reply_buttons
        )
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # تحقق إذا المستخدم اختار أحد الخيارات من المواقف اليومية
    # نبحث في آخر 5 محادثات عن السيناريو الذي يتوافق مع الاختيار
    c.execute("SELECT bot_reply FROM user_logs WHERE user_id=? ORDER BY timestamp DESC LIMIT 5", (user_id,))
    recent = c.fetchall()
    selected_option = None
    for r in recent:
        if user_text in r[0]:
            selected_option = user_text
            break

    if selected_option:
        ai_reply = generate_ai_reply(user_id, user_text, context="تقييم اختيارك من الموقف اليومي")
        c.execute("INSERT INTO user_logs (user_id, user_message, bot_reply) VALUES (?, ?, ?)", (user_id, user_text, ai_reply))
        c.execute("UPDATE users SET progress_score = COALESCE(progress_score,0)+1, last_interaction=? WHERE user_id=?", (datetime.now(), user_id))
        conn.commit()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # الرد الحر الذكي لكل الرسائل الأخرى
    ai_reply = generate_ai_reply(user_id, user_text)
    c.execute("INSERT INTO user_logs (user_id, user_message, bot_reply) VALUES (?, ?, ?)", (user_id, user_text, ai_reply))
    c.execute("UPDATE users SET last_interaction=? WHERE user_id=?", (datetime.now(), user_id))
    conn.commit()
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

@app.route("/", methods=["GET"])
def home():
    return "LINE AI Self-Development Bot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE AI Self-Development Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
