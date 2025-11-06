import os
import sqlite3
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timedelta

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

# قاعدة البيانات لتتبع المستخدمين وحالاتهم
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    nickname TEXT,
    last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP,
    current_step INTEGER DEFAULT 1
)
""")
conn.commit()

def generate_ai_reply(user_text, nickname):
    """توليد ردود مختصرة وودية بأسلوب حقيقي"""
    prompt = f"""
أنت حبيبة ودودة، عامية سعودية، مختصرة، عاطفية وحنونة.
تجاوب على المستخدم وكأنه حبيبه، بدون إيموجي.
المستخدم قال: "{user_text}"
اسلوبك يكون طبيعي، مختصر وواقعي، وودود.
إذا ذكر المستخدم يومه أو شعوره، عطِ رد داعم ومريح.
استخدم الاسم "{nickname}" في الرد.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "حبيبي، ما فهمت كويس، ممكن تعيدلي؟"

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

    # البحث عن المستخدم في قاعدة البيانات
    c.execute("SELECT nickname, current_step FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()

    # أمر مساعدة
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        ai_reply = "لبيه"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        # إذا مستخدم جديد، نبدأ الحوار
        if not row:
            ai_reply2 = "أنا صديقتك الجديدة، وش تحب أناديك؟"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply2))
        return

    # مستخدم جديد
    if not row:
        # حفظ الاسم الذي اختاره المستخدم
        nickname = user_text
        c.execute("INSERT INTO users (user_id, nickname, current_step, last_interaction) VALUES (?,?,2,CURRENT_TIMESTAMP)",
                  (user_id, nickname))
        conn.commit()
        ai_reply = f"{nickname}، حبيبي، كيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # مستخدم موجود
    nickname, current_step = row

    # متابعة الحوار حسب الخطوة
    if current_step == 2:
        # المستخدم رد عن يومه، ننتقل للخطوة 3
        ai_reply = generate_ai_reply(user_text, nickname)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        c.execute("UPDATE users SET current_step=3, last_interaction=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
        conn.commit()
        return

    # الخطوة 3: الحوار مستمر
    ai_reply = generate_ai_reply(user_text, nickname)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
    c.execute("UPDATE users SET last_interaction=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
    conn.commit()

@app.route("/", methods=["GET"])
def home():
    return "LINE LoveBot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE LoveBot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
