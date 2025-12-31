from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import google.generativeai as genai
import os
from datetime import datetime, timedelta
import json

app = Flask(__name__)

# إعدادات LINE
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# مفاتيح Gemini API
GEMINI_KEYS = [
    os.getenv('GEMINI_API_KEY_1'),
    os.getenv('GEMINI_API_KEY_2'),
    os.getenv('GEMINI_API_KEY_3')
]

# ملف لتتبع استخدام المفاتيح
KEY_STATUS_FILE = 'key_status.json'

def load_key_status():
    """تحميل حالة المفاتيح"""
    if os.path.exists(KEY_STATUS_FILE):
        with open(KEY_STATUS_FILE, 'r') as f:
            return json.load(f)
    return {'current_key_index': 0, 'last_reset': datetime.now().isoformat()}

def save_key_status(status):
    """حفظ حالة المفاتيح"""
    with open(KEY_STATUS_FILE, 'w') as f:
        json.dump(status, f)

def get_active_gemini_client():
    """الحصول على مفتاح Gemini نشط"""
    status = load_key_status()
    current_index = status.get('current_key_index', 0)
    failed_keys = status.get('failed_keys', [])
    
    # إعادة تعيين يومياً
    last_reset = datetime.fromisoformat(status.get('last_reset', datetime.now().isoformat()))
    if datetime.now() - last_reset > timedelta(days=1):
        current_index = 0
        failed_keys = []
        status = {
            'current_key_index': 0, 
            'last_reset': datetime.now().isoformat(),
            'failed_keys': []
        }
        save_key_status(status)
    
    # جرب المفتاح الحالي أولاً
    for i in range(len(GEMINI_KEYS)):
        key_index = (current_index + i) % len(GEMINI_KEYS)
        
        # تجاهل المفاتيح الفاشلة
        if key_index in failed_keys:
            continue
        
        try:
            genai.configure(api_key=GEMINI_KEYS[key_index])
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            
            # تحديث المفتاح النشط
            if key_index != current_index:
                status['current_key_index'] = key_index
                save_key_status(status)
                print(f"تم التبديل للمفتاح رقم {key_index + 1}")
            
            return model
        except Exception as e:
            error_msg = str(e).lower()
            if "quota" in error_msg or "limit" in error_msg or "resource" in error_msg:
                print(f"المفتاح {key_index + 1} وصل للحد اليومي")
                if key_index not in failed_keys:
                    failed_keys.append(key_index)
                    status['failed_keys'] = failed_keys
                    save_key_status(status)
                continue
            else:
                print(f"خطأ في المفتاح {key_index + 1}: {e}")
                continue
    
    raise Exception("جميع مفاتيح API وصلت للحد اليومي")

def get_coach_response(user_message, user_id):
    """الحصول على رد من مدرب الحياة"""
    max_retries = len(GEMINI_KEYS)
    
    for attempt in range(max_retries):
        try:
            model = get_active_gemini_client()
            
            system_prompt = """أنت مدربة حياة شخصية رقمية ومحفزة.

خصائصك:
- تتحدثين بأسلوب صديق مقرب وداعم
- ردودك مختصرة ومباشرة (2-4 جمل فقط)
- لا تستخدمين الإيموجي نهائياً
- تقدمين الدعم النفسي والتحفيز
- تساعدين في وضع الأهداف وتحقيقها
- تستمعين بعمق وتفهمين المشاعر
- تطرحين أسئلة تحفيزية عندما يكون مناسباً

أسلوبك في الرد:
- مباشر وواضح
- محفز وإيجابي
- قصير ومركز
- بدون إيموجي أبداً

تذكري: أنت صديقة تدعم وتحفز، ليست معالجة نفسية."""

            chat = model.start_chat(history=[])
            
            full_prompt = f"{system_prompt}\n\nالرسالة: {user_message}\n\nالرد:"
            
            response = chat.send_message(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.9,
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=200,
                )
            )
            
            return response.text.strip()
            
        except Exception as e:
            error_msg = str(e).lower()
            print(f"خطأ في المحاولة {attempt + 1}: {e}")
            
            if "quota" in error_msg or "limit" in error_msg or "resource" in error_msg:
                # علّم المفتاح الحالي كفاشل وحاول المفتاح التالي
                status = load_key_status()
                current_key = status.get('current_key_index', 0)
                failed_keys = status.get('failed_keys', [])
                
                if current_key not in failed_keys:
                    failed_keys.append(current_key)
                    status['failed_keys'] = failed_keys
                    status['current_key_index'] = (current_key + 1) % len(GEMINI_KEYS)
                    save_key_status(status)
                
                if attempt < max_retries - 1:
                    continue
                else:
                    return "عذراً، الخدمة مشغولة حالياً. حاول بعد قليل."
            else:
                return "عذراً، حصل خطأ مؤقت. حاول مرة أخرى."
    
    return "عذراً، الخدمة غير متوفرة حالياً. حاول لاحقاً."

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    user_id = event.source.user_id
    
    # الحصول على الرد من Gemini
    reply_text = get_coach_response(user_message, user_id)
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

@app.route("/", methods=['GET'])
def home():
    return "Life Coach Bot is running!"

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
