import os
import random
from flask import Flask, request
import google_genai as genai
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import TextSendMessage

app = Flask(__name__)

# =========================
# مفاتيح Gemini (ضعها في .env)
# =========================
GEMINI_KEYS = [
    os.environ.get("GEMINI_API_KEY_1"),
    os.environ.get("GEMINI_API_KEY_2"),
    os.environ.get("GEMINI_API_KEY_3")
]

current_key_index = 0

def set_gemini_key():
    global current_key_index
    key = GEMINI_KEYS[current_key_index]
    genai.configure(api_key=key)

# تعيين أول مفتاح
set_gemini_key()

# =========================
# إعداد Line Bot
# =========================
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def home():
    return "Server is running!", 200

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400

    return "OK", 200

# =========================
# التعامل مع رسائل المستخدم
# =========================
@handler.add_message("text")
def handle_message(event):
    user_message = event.message.text
    response_text = generate_gemini_reply(user_message)
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response_text)
    )

# =========================
# توليد الردود من Gemini
# =========================
def generate_gemini_reply(prompt: str) -> str:
    global current_key_index
    for attempt in range(len(GEMINI_KEYS)):
        try:
            response = genai.chat(
                model="chat-bison-001",
                messages=[
                    {"role": "system", "content": "أنت صديقة داعمة، مختصرة، توجه المستخدم للصح."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_output_tokens=400
            )
            return response.candidates[0].content

        except genai.errors.BadRequestError:
            # إذا انتهى الحد اليومي للمفتاح
            current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
            set_gemini_key()
        except Exception as e:
            print(f"Gemini error: {e}")
            return "عذرًا، حدث خطأ أثناء توليد الرد."

    return "عذرًا، جميع المفاتيح تجاوزت الحد اليومي."

# =========================
# تشغيل السيرفر
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
