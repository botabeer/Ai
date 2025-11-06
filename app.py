import os
import sqlite3
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
    "max_output_tokens": 2000,
}

# إعداد قاعدة البيانات
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    nickname TEXT,
    first_interaction INTEGER DEFAULT 1
)
""")
conn.commit()

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

    # التحقق من وجود المستخدم في قاعدة البيانات
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()

    # أول مرة يرسل المستخدم المساعدة أو /start
    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        if not user:
            ai_reply = "لبيه! أنا صديقتك الجديدةأختار لي اسم تحبه؟!"
            c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
        else:
            ai_reply = "مرحبًا من جديد! تحب نبدأ بحوار اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # إذا المستخدم يرسل اسمه لأول مرة
    if user and user[2] == 1:  # first_interaction = 1
        nickname = user_text
        c.execute("UPDATE users SET nickname=?, first_interaction=0 WHERE user_id=?", (nickname, user_id))
        conn.commit()
        ai_reply = f"تمام! الحين صار لك اسم، {nickname}. كيف كان يومك اليوم؟ تحب تحكين لي شوي عن يومك؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # الردود العامة: تفاعل ذكاء اصطناعي ودّي
    prompt = f"""
أنت صديقة ودية ومهتمة، تحاور المستخدم بأسلوب مختصر وواقعي، تركز على تعزيز ثقته بنفسه وتقوية شخصيته.
المستخدم كتب: {user_text}
أجب بأسلوب ودّي ولبق وكأنك صديقة مقربة، وادمج نصيحة قصيرة لتحسين النفس.
"""
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        ai_reply = response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        ai_reply = "عذرًا، حصل خطأ أثناء محاولة الرد، جرب مرة ثانية."

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

@app.route("/", methods=["GET"])
def home():
    return "LINE FriendlyBot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
