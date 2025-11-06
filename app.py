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
import json
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
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 2000,
}

# قاعدة البيانات
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    personality TEXT,
    mood TEXT,
    progress_score INTEGER DEFAULT 0
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS user_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    module TEXT,
    content TEXT,
    user_choice TEXT,
    analysis TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS weekly_challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    challenge TEXT,
    success INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

def generate_ai_content(prompt):
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "نعتذر، لم نتمكن من توليد المحتوى الآن."

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

def get_quick_replies():
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="🌱 مرآة", text="🌱 مرآة")),
        QuickReplyButton(action=MessageAction(label="💬 أسلوبك", text="💬 أسلوبك")),
        QuickReplyButton(action=MessageAction(label="🧠 موقف", text="🧠 موقف")),
        QuickReplyButton(action=MessageAction(label="✨ وعي", text="✨ وعي")),
        QuickReplyButton(action=MessageAction(label="🎯 تحدي", text="🎯 تحدي")),
        QuickReplyButton(action=MessageAction(label="💌 إعادة", text="💌 إعادة")),
        QuickReplyButton(action=MessageAction(label="🧠 لعبة", text="🧠 لعبة")),
    ])

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        ai_reply = (
            "أهلاً بك 🌟\n"
            "اختر تجربة اليوم:\n"
            "🌱 مرآة\n"
            "💬 أسلوبك\n"
            "🧠 موقف\n"
            "✨ وعي\n"
            "🎯 تحدي\n"
            "💌 إعادة\n"
            "🧠 لعبة\n\n"
            "كل خيار سيأخذك لتجربة مفيدة وودّية 🌸"
        )
        reply = TextSendMessage(text=ai_reply, quick_reply=get_quick_replies())
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # 🌱 مرآة
    if user_text.lower() in ["🌱 مرآة", "مرآة"]:
        prompt = "اصنع سؤال تأملي يومي قصير عن الذات والمشاعر بطريقة ودية وملهمة."
        text = generate_ai_content(prompt)
        reply = TextSendMessage(text=f"🌱 مرآة اليوم:\n{text}", quick_reply=get_quick_replies())
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # 💬 أسلوبك
    if user_text.lower() in ["💬 أسلوبك", "أسلوبك"]:
        prompt = "اقترح إعادة صياغة ودية ومهذبة لأي جملة سلبية يرسلها المستخدم."
        text = generate_ai_content(prompt)
        reply = TextSendMessage(text=f"💬 لمسة كلام:\n{text}", quick_reply=get_quick_replies())
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # 🧠 موقف / 🧠 لعبة
    if user_text.lower() in ["🧠 موقف", "موقف", "🧠 لعبة", "لعبة"]:
        prompt = """
        اصنع موقف اجتماعي قصير لتطوير الذات والعلاقات، بصيغة JSON:
        {
            "scenario": "...",
            "options": {"A": "...", "B": "...", "C": "..."},
            "analysis": {"A": "...", "B": "...", "C": "..."},
            "best_solution": "A/B/C",
            "practical_advice": "..."
        }
        """
        json_text = generate_ai_content(prompt)
        try:
            scenario = json.loads(json_text)
            options_text = "\n".join([f"{k}) {v}" for k,v in scenario["options"].items()])
            analysis_text = "\n".join([f"{k}) {v}" for k,v in scenario["analysis"].items()])
            reply_text = (
                f"🧠 موقف اليوم:\n{scenario['scenario']}\n\n"
                f"خيارات:\n{options_text}\n\n"
                f"تحليل:\n{analysis_text}\n\n"
                f"الحل الأمثل: {scenario['best_solution']}\n"
                f"نصائح عملية: {scenario['practical_advice']}"
            )
        except:
            reply_text = "عذراً، حدث خطأ في توليد الموقف. حاول مرة أخرى."
        reply = TextSendMessage(text=reply_text, quick_reply=get_quick_replies())
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # ✨ وعي
    if user_text.lower() in ["✨ وعي", "وعي"]:
        prompt = "اكتب رسالة قصيرة تحفيزية لتعزيز الذكاء العاطفي والوعي الذاتي."
        text = generate_ai_content(prompt)
        reply = TextSendMessage(text=f"✨ لمحة وعي:\n{text}", quick_reply=get_quick_replies())
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # 🎯 تحدي
    if user_text.lower() in ["🎯 تحدي", "تحدي"]:
        prompt = "اصنع تحدي أسبوعي قصير مع نصائح يومية لتحسين الذات، بطريقة ودية."
        challenge = generate_ai_content(prompt)
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="✅ نجحت اليوم", text="نجحت اليوم")),
            QuickReplyButton(action=MessageAction(label="❌ فشلت اليوم", text="فشلت اليوم"))
        ])
        reply = TextSendMessage(text=f"🎯 تحدي:\n{challenge}", quick_reply=quick_reply_buttons)
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # 💌 إعادة
    if user_text.lower().startswith("💌 إعادة:") or user_text.lower().startswith("إعادة:"):
        sentence = user_text.split(":", 1)[1].strip() if ":" in user_text else ""
        if sentence:
            prompt = f"حوّل الجملة التالية إلى صياغة إيجابية وبناءة: {sentence}"
            positive_sentence = generate_ai_content(prompt)
            reply = TextSendMessage(text=f"💌 إعادة صياغة:\n{positive_sentence}", quick_reply=get_quick_replies())
            line_bot_api.reply_message(event.reply_token, reply)
            return
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="💌 أرسل الجملة بعد 'إعادة:'"))
            return

    # الرد الافتراضي
    reply = TextSendMessage(
        text="✨ مرحباً! اكتب /start لاختيار تجربة اليوم.", 
        quick_reply=get_quick_replies()
    )
    line_bot_api.reply_message(event.reply_token, reply)

@app.route("/", methods=["GET"])
def home():
    return "LINE SmartSelf AI Bot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE SmartSelf AI Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
