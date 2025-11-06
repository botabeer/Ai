import os
import logging
from datetime import datetime
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai
import time

# ===== Logging =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("LoveBot")

# ===== تحميل المتغيرات =====
load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() == "true"

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    logger.error("Missing environment variables")
    raise ValueError("Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===== إعداد Gemini =====
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")
generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1000,
}

# ===== توليد رد AI مع إعادة المحاولة =====
def generate_ai_reply(user_text, nickname="حبيبي", retries=3):
    prompt = f"""
أنت حبيبة ودودة، تتكلم بعامية سعودية، مختصرة وعاطفية، بدون أي إيموجي.
المستخدم ({nickname}) قال: "{user_text}"
رد مختصر جداً (سطر أو سطرين) حنون وعاطفي.
"""
    attempt = 0
    while attempt < retries:
        try:
            response = model.generate_content(prompt, generation_config=generation_config)
            text = response.text.strip()
            if len(text) > 500:
                text = text[:500] + "..."
            return text
        except Exception as e:
            attempt += 1
            logger.warning(f"Attempt {attempt} failed: {e}")
            time.sleep(0.5)
    logger.error("All attempts to generate AI reply failed")
    return f"{nickname}، حصل خطأ حاول مرة ثانية"

# ===== Flask routes =====
app = Flask(__name__)

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        logger.warning("Missing X-Line-Signature")
        return "Missing signature", 400
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.warning("Invalid signature")
        return "Invalid signature", 400
    except Exception as e:
        logger.error(f"Error in callback: {e}")
        return "Internal error", 500
    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id

    if not user_text:
        if DEBUG_MODE:
            logger.info(f"Empty message from {user_id}")
        return

    logger.info(f"Received message from {user_id}: {user_text}")

    # أمر التشغيل لاختبار البوت
    if user_text.lower() in ["تشغيل", "/test", "/ping"]:
        try:
            _ = generate_ai_reply("هل AI يعمل؟")
            reply = "تم تشغيل البوت بنجاح"
        except:
            reply = "حدث خطأ أثناء تشغيل البوت"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # الرد مباشرة بدون حفظ المستخدم
    ai_reply = generate_ai_reply(user_text, nickname="حبيبي")
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_reply))
    logger.info(f"Replied to {user_id}: {ai_reply}")

@app.route("/", methods=["GET"])
def home():
    return "LINE LoveBot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    logger.info(f"Starting LINE LoveBot on port {port} (DEBUG={DEBUG_MODE})...")
    app.run(host="0.0.0.0", port=port, debug=DEBUG_MODE)
