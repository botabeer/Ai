import os
import sqlite3
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, QuickReply, QuickReplyButton, MessageAction
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
import random

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
    "max_output_tokens": 1000,  # مختصر
}

# إعداد قاعدة البيانات
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    mood TEXT,
    progress_score INTEGER DEFAULT 0
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS user_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    scenario TEXT,
    choice TEXT,
    analysis TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()

# أمثلة مواقف يومية
scenarios = [
    {
        "scenario": "أثناء اجتماع عمل، يحاول زميل مقاطعتك بطريقة حادة.",
        "options": [
            {"text": "الرد بغضب", "analysis": "يقلل الاحترام والكاريزما", "is_correct": False},
            {"text": "التوقف بهدوء والرد لبقًا", "analysis": "يعزز الكاريزما والثقة", "is_correct": True},
            {"text": "تجاهل المقاطعة", "analysis": "يحافظ على هدوءك ولكنه قد يفسر كضعف", "is_correct": True}
        ]
    },
    {
        "scenario": "فقدت صديقًا مقربًا وتشعر بالحزن والوحدة.",
        "options": [
            {"text": "الانعزال عن الجميع", "analysis": "قد يزيد شعور الحزن والوحدة", "is_correct": False},
            {"text": "التحدث مع صديق موثوق أو مستشار", "analysis": "يعزز الذكاء العاطفي والدعم النفسي", "is_correct": True},
            {"text": "الانشغال بالعمل أو الهوايات لتجنب التفكير", "analysis": "جزئيًا صحيح لكنه لا يحل المشاعر الأساسية", "is_correct": True}
        ]
    }
]

def generate_ai_response(prompt):
    """توليد رد مختصر وودّي باستخدام Gemini AI"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        text = response.text.strip()
        # تقليل طول الرد
        if len(text) > 400:
            text = text[:400] + "..."
        return text
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "عذرًا، لم أتمكن من توليد رد الآن، حاول لاحقًا."

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

    # بداية المحادثة بأسلوب غامض
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        ai_reply = "كيف تشعر اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # إرسال موقف اليوم
    if user_text.lower() == "موقف اليوم":
        scenario_obj = random.choice(scenarios)
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

    # التعامل مع أي رد من المستخدم بأسلوب ودّي مختصر
    prompt = f"""
أنت صديق ودّي للمستخدم، ترد بأسلوب مختصر، مفيد، وكأنك تحاور شخصًا وجهًا لوجه.
ركز على فهم شعوره وتعزيز ثقته بنفسه.
المستخدم قال: {user_text}
"""
    ai_reply = generate_ai_response(prompt)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

@app.route("/", methods=["GET"])
def home():
    return "LINE SmartSelf AI Bot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
