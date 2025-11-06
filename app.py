import os
import sqlite3
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, QuickReply, QuickReplyButton, MessageAction
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timedelta
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
    scenario TEXT,
    choice TEXT,
    analysis TEXT,
    advice TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# أمثلة مواقف جاهزة
scenarios = [
    {
        "scenario": "أثناء اجتماع عمل، يحاول زميل مقاطعتك بطريقة حادة.",
        "options": [
            {"text": "الرد بغضب", "analysis": "يقلل الاحترام والكاريزما", "is_correct": False},
            {"text": "التوقف بهدوء والرد لبقًا", "analysis": "يعزز الكاريزما والثقة", "is_correct": True},
            {"text": "تجاهل المقاطعة", "analysis": "يحافظ على هدوءك ولكن قد يفسر كضعف", "is_correct": True}
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

def generate_ai_response(prompt_text):
    try:
        response = model.generate_content(prompt_text, generation_config=generation_config)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "عذرًا، لم أتمكن من توليد الرد الآن."

def generate_personalized_advice(user_id, scenario, choice):
    prompt = f"""
أنت مساعد ودّي لتطوير الشخصية والكاريزما. المستخدم اختار:
{choice}
الموقف كان:
{scenario}
قدم نصيحة عملية، ودية، ومناسبة لتعزيز الثقة بالنفس والكاريزما.
"""
    return generate_ai_response(prompt)

def generate_weekly_summary(user_id):
    one_week_ago = datetime.now() - timedelta(days=7)
    c.execute("""
        SELECT scenario, choice, analysis, advice
        FROM user_logs
        WHERE user_id=? AND timestamp>=?
    """, (user_id, one_week_ago))
    logs = c.fetchall()

    if not logs:
        return "لم يكن هناك نشاط كافٍ هذا الأسبوع، حاول متابعة المواقف اليومية لتحصل على الملخص الأسبوعي."

    correct_count = sum(1 for log in logs if "يعزز" in log[2] or "صحيح" in log[2])
    total = len(logs)
    advice_summary = "\n".join([f"- {log[3]}" for log in logs if log[3]])

    prompt = f"""
أنت مساعد ودّي. المستخدم تعامل مع {total} موقفًا هذا الأسبوع.
عدد الاختيارات الصحيحة: {correct_count}.
نصائح الأسبوع: {advice_summary}

قدم ملخصًا ودّيًا قصيرًا، مشجعًا، يوضح نقاط القوة ويعطي خطوات عملية لتعزيز الشخصية والثقة بالنفس.
"""
    return generate_ai_response(prompt)

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

    # بداية ودية وغامضة
    if user_text.lower() in ["start", "hi", "مرحبا", "/start"]:
        reply = "أنا هنا جنبك، تحب تحكي لي عن إحساسك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # الملخص الأسبوعي
    if user_text.lower() in ["ملخص الأسبوع", "week summary"]:
        summary = generate_weekly_summary(user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary))
        return

    # إرسال موقف اليوم
    if user_text.lower() == "موقف اليوم":
        scenario_obj = random.choice(scenarios)
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label=opt['text'], text=opt['text']))
            for opt in scenario_obj['options']
        ])
        # حفظ المستخدم
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        reply = TextSendMessage(
            text=scenario_obj['scenario'],
            quick_reply=quick_reply_buttons
        )
        line_bot_api.reply_message(event.reply_token, reply)
        return

    # معالجة اختيار المستخدم
    selected_scenario = None
    selected_option = None
    for s in scenarios:
        for opt in s['options']:
            if opt['text'] == user_text:
                selected_scenario = s['scenario']
                selected_option = opt
                break
        if selected_option:
            break

    if selected_option:
        advice = generate_personalized_advice(user_id, selected_scenario, selected_option['text'])
        # تسجيل الاختيار
        c.execute("""
        INSERT INTO user_logs (user_id, scenario, choice, analysis, advice)
        VALUES (?, ?, ?, ?, ?)
        """, (user_id, selected_scenario, selected_option['text'], selected_option['analysis'], advice))
        if selected_option['is_correct']:
            c.execute("UPDATE users SET progress_score = COALESCE(progress_score,0)+1 WHERE user_id=?", (user_id,))
        conn.commit()
        reply_text = f"تحليل اختيارك: {selected_option['analysis']}\n\nنصيحة: {advice}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    else:
        # الرد الذكي على أي رسالة أخرى
        prompt = f"""
أنت صديق ودّي ومساعد ذكي. المستخدم كتب:
{user_text}
رد عليه بطريقة ودية، ذكية، تشجعه، وتساعده على تقوية شخصيته والثقة بنفسه.
"""
        ai_reply = generate_ai_response(prompt)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

@app.route("/", methods=["GET"])
def home():
    return "LINE SmartSelf Bot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
