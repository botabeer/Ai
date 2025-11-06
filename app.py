import os
import logging
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import TextSendMessage, MessageEvent, TextMessage
from dotenv import load_dotenv
import google.generativeai as genai

# إعداد السجلات

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(**name**)

# تحميل المتغيرات البيئية

load_dotenv()

app = Flask(**name**)

# التحقق من المتغيرات البيئية

LINE_CHANNEL_ACCESS_TOKEN = os.getenv(“LINE_CHANNEL_ACCESS_TOKEN”)
LINE_CHANNEL_SECRET = os.getenv(“LINE_CHANNEL_SECRET”)
GEMINI_API_KEY = os.getenv(“GEMINI_API_KEY”)

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
raise ValueError(“Missing required environment variables”)

# إعداد LINE Bot

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# إعداد Gemini AI

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(“gemini-2.0-flash-exp”)

generation_config = {
“temperature”: 0.7,
“top_p”: 0.95,
“top_k”: 40,
“max_output_tokens”: 2000,
}

# دالة توليد الردود بالذكاء الاصطناعي

def generate_ai_reply(user_text):
“”“توليد رد من Gemini AI بناءً على رسالة المستخدم”””
try:
prompt = f”””
أنت صديقة ودودة وحنونة، تتكلم بالعربية العامية السعودية، مختصرة جداً (سطرين أو ثلاثة)، عاطفية وواقعية.
المستخدم قال: “{user_text}”

قواعد مهمة:

- الردود مختصرة وسطرين أو ثلاثة فقط
- بدون أي إيموجي أو رموز
- ودود وعاطفي وحبّي
- افهم شعوره ورد بطريقة صادقة وواقعية
- كل الردود من AI مباشرة، لا تستخدم أي نصوص جاهزة أو بدائل

رد فقط بالرسالة، بدون مقدمات.
“””
response = model.generate_content(prompt, generation_config=generation_config)
return response.text.strip()
except Exception as e:
logger.error(f”Error generating AI reply: {e}”)
return “عذراً، حصل خطأ. جرب مرة ثانية”

# معالج الرسائل النصية

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
“”“معالجة الرسائل النصية الواردة”””
user_text = event.message.text
logger.info(f”Received message: {user_text}”)

```
try:
    ai_reply = generate_ai_reply(user_text)
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=ai_reply)
    )
    logger.info(f"Sent reply: {ai_reply}")
except LineBotApiError as e:
    logger.error(f"LINE Bot API error: {e}")
except Exception as e:
    logger.error(f"Unexpected error: {e}")
```

# Webhook endpoint

@app.route(”/callback”, methods=[“POST”])
def callback():
“”“استقبال ومعالجة الـ webhook من LINE”””
# الحصول على التوقيع
signature = request.headers.get(“X-Line-Signature”)
if not signature:
logger.warning(“Missing X-Line-Signature header”)
abort(400)

```
# الحصول على محتوى الطلب
body = request.get_data(as_text=True)
logger.info(f"Request body: {body}")

# معالجة الـ webhook
try:
    handler.handle(body, signature)
except InvalidSignatureError:
    logger.error("Invalid signature. Check your channel secret.")
    abort(400)
except Exception as e:
    logger.error(f"Error handling webhook: {e}")
    abort(500)

return "OK", 200
```

# الصفحة الرئيسية

@app.route(”/”, methods=[“GET”])
def home():
“”“صفحة رئيسية بسيطة للتأكد من عمل البوت”””
return “””
<html>
<head><title>LINE AI LoveBot</title></head>
<body style="font-family: Arial; text-align: center; padding: 50px;">
<h1>🤖 LINE AI LoveBot</h1>
<p>البوت يعمل بنجاح!</p>
<p style="color: #06c755;">✓ Server is running</p>
</body>
</html>
“””, 200

# نقطة صحة التطبيق

@app.route(”/health”, methods=[“GET”])
def health():
“”“فحص صحة التطبيق”””
return {“status”: “healthy”, “service”: “LINE AI LoveBot”}, 200

if **name** == “**main**”:
port = int(os.getenv(“PORT”, 10000))
logger.info(f”Starting server on port {port}”)
app.run(host=“0.0.0.0”, port=port, debug=False)
