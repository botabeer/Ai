import os
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import google.generativeai as genai

# تحميل المتغيرات البيئية
load_dotenv()

app = Flask(__name__)

# إعداد المتغيرات
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# التحقق من وجود المتغيرات المطلوبة
if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

# إعداد LINE Bot
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# إعداد Gemini (يجب أن يكون قبل إنشاء النموذج)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")

# إعدادات السلامة والجيل (اختياري)
generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 2000,
}

@app.route("/callback", methods=["POST"])
def callback():
    """نقطة النهاية لاستقبال رسائل LINE"""
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
    """معالجة الرسائل النصية"""
    user_text = event.message.text.strip()
    
    # التحقق من طول الرسالة
    if len(user_text) > 2000:
        ai_reply = "عذراً، الرسالة طويلة جداً. الرجاء إرسال رسالة أقصر."
    elif not user_text:
        ai_reply = "الرجاء إرسال رسالة نصية."
    # رسائل المساعدة
    elif user_text.lower() in ["مساعدة", "help", "/help", "/start"]:
        ai_reply = (
            "مرحباً! أنا بوت ذكاء اصطناعي\n\n"
            "يمكنك أن تسألني عن أي شيء:\n"
            "• أسئلة عامة\n"
            "• برمجة وتقنية\n"
            "• ترجمة\n"
            "• شرح مفاهيم\n"
            "• كتابة نصوص\n\n"
            "فقط أرسل سؤالك وسأجيبك فوراً."
        )
    # معالجة الأسئلة بـ Gemini
    else:
        try:
            # إضافة سياق للذكاء الاصطناعي
            prompt = f"""أنت مساعد ذكي ومفيد على LINE. أجب على السؤال التالي بشكل واضح ومختصر ومفيد.
إذا كان السؤال بالعربية، أجب بالعربية. إذا كان بالإنجليزية، أجب بالإنجليزية.

السؤال: {user_text}"""
            
            response = model.generate_content(
                prompt,
                generation_config=generation_config
            )
            
            ai_reply = response.text.strip()
            
            # التحقق من طول الرد (LINE لديه حد أقصى 5000 حرف)
            if len(ai_reply) > 4900:
                ai_reply = ai_reply[:4900] + "\n\n... (تم اختصار الرد)"
                
        except Exception as e:
            print(f"Gemini API Error: {e}")
            ai_reply = (
                "عذراً، حدث خطأ أثناء معالجة طلبك.\n"
                "الرجاء المحاولة مرة أخرى."
            )
    
    # إرسال الرد
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=ai_reply)
        )
    except Exception as e:
        print(f"Error sending message: {e}")

@app.route("/", methods=["GET"])
def home():
    """صفحة رئيسية بسيطة للتحقق من عمل السيرفر"""
    return "LINE Bot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    """نقطة فحص الصحة"""
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting LINE Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
