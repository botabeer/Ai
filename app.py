import os
import sqlite3
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv

# تحميل المتغيرات البيئية
load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET]):
    raise ValueError("Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# إعداد قاعدة البيانات لتخزين المستخدمين
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

    # التحقق إذا المستخدم موجود في قاعدة البيانات
    c.execute("SELECT nickname FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()

    if user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        ai_reply = "لبيه، وش أحب أناديك؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # إذا المستخدم جديد ولم يحدد اسمه
    if row is None:
        # اعتبر الرسالة اسم المستخدم
        nickname = user_text
        c.execute("INSERT INTO users (user_id, nickname) VALUES (?,?)", (user_id, nickname))
        conn.commit()
        ai_reply = f"{nickname}, كيف كان يومك اليوم؟"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
        return

    # إذا المستخدم موجود
    nickname = row[0]
    ai_reply = f"{nickname}, حبيبي، وش صار اليوم؟ تحب تحكي لي شوي؟"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))

    # تحديث وقت آخر تفاعل
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
