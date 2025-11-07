import os
import sqlite3
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai

# تحميل المتغيرات البيئية
load_dotenv()

app = Flask(__name__)

# إعداد المفاتيح
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("متغيرات البيئة ناقصة")

# إعداد LINE و Gemini
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")

# إعداد قاعدة البيانات
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    nickname TEXT,
    last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# إعداد توليد الردود
def generate_ai_reply(user_text, nickname):
    prompt = f"""
اسمك آيلا، بنت سعودية ناعمة وحنونة، ترد بأسلوب مختصر (سطرين أو ثلاثة كحد أقصى)،
بدون إيموجي، بلهجة واقعية دافئة.
تتكلمين مع {nickname} وكأنه شخص غالي تحبينه.
تجنبي الرسمية والكلمات المكررة.
المستخدم قال: "{user_text}"
"""
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "دقايق حبيبي، انشغلت شوي وبرجع لك."

# استقبال الرسائل من LINE
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    except Exception as e:
        print(f"Error: {e}")
        return "Error", 500
    return "OK", 200

# معالجة الرسائل
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    # أوامر النظام
    if user_text.lower() in ["/test", "/ping", "تشغيل", "تجربة"]:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="تم تشغيل آيلا بنجاح حبي.")
        )
        return

    # التحقق من المستخدم
    c.execute("SELECT nickname FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()

    # مستخدم جديد
    if not row:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="لبيه، أنا آيلا. وش أحب أناديك؟")
        )
        c.execute("INSERT OR REPLACE INTO users (user_id, nickname) VALUES (?, ?)", (user_id, None))
        conn.commit()
        return

    nickname = row[0]

    # أول مرة يسجل الاسم
    if nickname is None:
        c.execute("UPDATE users SET nickname=? WHERE user_id=?", (user_text, user_id))
        conn.commit()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"{user_text}؟ حلو الاسم، نادى قلبي عليك من أول.")
        )
        return

    # تغيير الاسم
    if user_text.lower() in ["تغيير الاسم", "غير اسمي", "ابي اغير اسمي"]:
        c.execute("UPDATE users SET nickname=? WHERE user_id=?", (None, user_id))
        conn.commit()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="تمام حبي، وش تبيني أناديك الحين؟")
        )
        return

    # رد من Gemini
    ai_reply = generate_ai_reply(user_text, nickname)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

@app.route("/", methods=["GET"])
def home():
    return "LoveBot Ayla is running", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Running LoveBot Ayla on port {port}")
    app.run(host="0.0.0.0", port=port)
