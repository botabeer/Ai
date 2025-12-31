import os
import random
from flask import Flask, request
import google.genai as genai
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import TextSendMessage

# ==========================
# إعداد التطبيق
# ==========================
app = Flask(__name__)

# قائمة مفاتيح Gemini
GEMINI_KEYS = [
    os.environ.get("GEMINI_API_KEY_1"),
    os.environ.get("GEMINI_API_KEY_2"),
    os.environ.get("GEMINI_API_KEY_3")
]

current_key_index = 0

def set_gemini_key():
    """تعيين مفتاح Gemini الحالي"""
    global current_key_index
    key = GEMINI_KEYS[current_key_index]
    genai.configure(api_key=key)

# تعيين أول مفتاح تلقائيًا عند بداية التطبيق
set_gemini_key()

# إعداد Line Bot
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ==========================
# نقطة النهاية للتأكد من السيرفر
# ==========================
@app.route("/", methods=["GET"])
def home():
    return "Server is running!", 200

# ==========================
# Webhook للرد على Line Messages
# ==========================
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400

    return "OK", 200

# ==========================
# معالجة الرسائل من المستخدم
# ==========================
@handler.add_message("text")
def handle_message(event):
    user_message = event.message.text
    response_text = generate_gemini_reply(user_message)
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response_text)
    )

# ==========================
# توليد رد من Gemini مع تبديل المفاتيح تلقائيًا
# ==========================
def generate_gemini_reply(prompt: str) -> str:
    global current_key_index

    for attempt in range(len(GEMINI_KEYS)):
        try:
            response = genai.ChatCompletion.create(
                model="chat-bison-001",
                messages=[
                    {"role": "system", "content": "أنت صديقة داعمة، مختصرة، توجه المستخدم للصح."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_output_tokens=400
            )
            return response.candidates[0].content

        except genai.errors.BadRequestError as e:
            # إذا انتهى الحد اليومي للمفتاح، ننتقل للمفتاح التالي
            current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
            set_gemini_key()
        except Exception as e:
            print(f"Gemini error: {e}")
            return "عذرًا، حدث خطأ أثناء توليد الرد."

    return "عذرًا، جميع المفاتيح تجاوزت الحد اليومي."

# ==========================
# تشغيل السيرفر
# ==========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
