"""
🤖 Life Coach LINE Bot - Stable Edition
========================================
نسخة مستقرة ومختبرة
"""

from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
import google.generativeai as genai
import os
from datetime import datetime, timedelta
from collections import defaultdict, deque
import logging
import time

# ================== Logging ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ================== التطبيق ==================
app = Flask(__name__)

# ================== إعدادات LINE ==================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ================== مفاتيح Gemini ==================
GEMINI_KEYS = [
    os.getenv('GEMINI_API_KEY_1'),
    os.getenv('GEMINI_API_KEY_2'),
    os.getenv('GEMINI_API_KEY_3')
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k and not k.startswith('your_')]

logger.info(f"🔑 عدد المفاتيح المتاحة: {len(GEMINI_KEYS)}")

# ================== الذاكرة البسيطة ==================
class SimpleMemory:
    def __init__(self):
        self.conversations = defaultdict(lambda: deque(maxlen=6))
        
    def add_message(self, user_id: str, role: str, content: str):
        self.conversations[user_id].append({
            'role': role,
            'content': content[:100]  # احتفظ بـ 100 حرف فقط
        })
        
    def get_history(self, user_id: str) -> str:
        history = list(self.conversations[user_id])[-3:]
        if not history:
            return ""
        
        formatted = []
        for msg in history:
            role = "المستخدم" if msg['role'] == 'user' else "أنتِ"
            formatted.append(f"{role}: {msg['content']}")
        return "\n".join(formatted)

# ================== إدارة المفاتيح ==================
class KeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.current_index = 0
        self.failed_keys = set()
        
    def get_next_key(self):
        attempts = 0
        while attempts < len(self.keys):
            if self.current_index not in self.failed_keys:
                key = self.keys[self.current_index]
                key_index = self.current_index
                self.current_index = (self.current_index + 1) % len(self.keys)
                return key, key_index
            
            self.current_index = (self.current_index + 1) % len(self.keys)
            attempts += 1
        
        raise Exception("جميع المفاتيح فشلت")
    
    def mark_failed(self, key_index: int):
        self.failed_keys.add(key_index)
        logger.warning(f"❌ المفتاح {key_index + 1} فشل")

# ================== تهيئة ==================
memory = SimpleMemory()
key_manager = KeyManager(GEMINI_KEYS)

# ================== قائمة النماذج (مرتبة حسب الأفضلية) ==================
MODELS_TO_TRY = [
    'gemini-1.5-flash',
    'gemini-1.5-flash-latest',
    'gemini-pro',
    'gemini-1.5-pro-latest'
]

# ================== البحث عن نموذج يعمل ==================
def find_working_model():
    """يبحث عن أول نموذج يعمل"""
    for key_idx in range(len(GEMINI_KEYS)):
        try:
            key = GEMINI_KEYS[key_idx]
            genai.configure(api_key=key)
            
            for model_name in MODELS_TO_TRY:
                try:
                    logger.info(f"🧪 اختبار: {model_name} مع المفتاح {key_idx + 1}")
                    
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(
                        "Hi",
                        generation_config=genai.types.GenerationConfig(
                            max_output_tokens=10,
                            temperature=0.9
                        )
                    )
                    
                    if response.text:
                        logger.info(f"✅ النموذج {model_name} يعمل!")
                        return model_name, key_idx
                        
                except Exception as e:
                    error_str = str(e).lower()
                    if "404" in error_str:
                        logger.info(f"⏭️ النموذج {model_name} غير متوفر")
                        continue
                    elif "quota" in error_str or "limit" in error_str:
                        logger.warning(f"⚠️ المفتاح {key_idx + 1} وصل للحد")
                        break
                    else:
                        logger.error(f"❌ خطأ: {str(e)[:100]}")
                        continue
                        
        except Exception as e:
            logger.error(f"❌ خطأ في المفتاح {key_idx + 1}: {str(e)[:100]}")
            continue
    
    return None, None

# ابحث عن نموذج يعمل عند البدء
WORKING_MODEL = None
WORKING_KEY_INDEX = None

try:
    logger.info("🔍 جاري البحث عن نموذج يعمل...")
    WORKING_MODEL, WORKING_KEY_INDEX = find_working_model()
    
    if WORKING_MODEL:
        logger.info(f"🎉 تم! النموذج: {WORKING_MODEL} | المفتاح: {WORKING_KEY_INDEX + 1}")
    else:
        logger.error("❌ لم نجد أي نموذج يعمل!")
except Exception as e:
    logger.error(f"❌ خطأ في البحث: {e}")

# ================== المحرك الرئيسي ==================
def get_ai_response(user_id: str, message: str) -> str:
    """يحصل على رد من Gemini"""
    
    if not WORKING_MODEL:
        return "عذراً، الخدمة غير متاحة حالياً. جاري العمل على حل المشكلة 🔧"
    
    # بناء السياق
    history = memory.get_history(user_id)
    
    prompt = f"""أنت "نور" - مدربة حياة شخصية ودودة.

{"آخر رسائل:" if history else ""}
{history}

الرسالة الحالية: {message}

رد بـ 2-3 جمل فقط، كوني طبيعية ودافئة. بدون إيموجي."""

    # جرب مع المفتاح والنموذج اللي شغالين
    for attempt in range(3):
        try:
            logger.info(f"🔄 محاولة {attempt + 1}/3")
            
            key = GEMINI_KEYS[WORKING_KEY_INDEX]
            genai.configure(api_key=key)
            
            model = genai.GenerativeModel(
                WORKING_MODEL,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.9,
                    top_p=0.95,
                    max_output_tokens=200,
                )
            )
            
            response = model.generate_content(prompt)
            reply = response.text.strip()
            
            # حفظ في الذاكرة
            memory.add_message(user_id, 'user', message)
            memory.add_message(user_id, 'assistant', reply)
            
            logger.info(f"✅ رد ناجح!")
            return reply
            
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"❌ محاولة {attempt + 1} فشلت: {str(e)[:100]}")
            
            if "quota" in error_msg or "limit" in error_msg or "resource" in error_msg:
                return "وصلنا للحد اليومي 😊 جربي غداً أو تواصلي مع المطور لإضافة مفاتيح"
            
            if attempt < 2:
                time.sleep(1)
                continue
            else:
                return "عذراً، حصل خطأ مؤقت. جربي مرة ثانية 🌸"
    
    return "عذراً، لا أستطيع الرد الآن 💭"

# ================== معالجات LINE ==================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Callback error: {e}")
        abort(500)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_id = event.source.user_id
        message = event.message.text.strip()
        
        logger.info(f"📨 من {user_id[:8]}: {message[:50]}")
        
        reply = get_ai_response(user_id, message)
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
        
        logger.info(f"✅ رد مرسل")
        
    except Exception as e:
        logger.error(f"❌ خطأ: {e}")

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    logger.info(f"🎉 متابع جديد: {user_id}")
    
    welcome = """مرحباً! أنا نور 🌟

أنا هنا لأدعمك في رحلتك.
شاركيني أي شيء في بالك."""
    
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=welcome)]
                )
            )
    except Exception as e:
        logger.error(f"خطأ في الترحيب: {e}")

# ================== نقاط النهاية ==================
@app.route("/", methods=['GET'])
def home():
    return jsonify({
        'status': 'running',
        'bot': 'Life Coach Bot',
        'model': WORKING_MODEL or 'none',
        'key_index': WORKING_KEY_INDEX + 1 if WORKING_KEY_INDEX is not None else 0,
        'users': len(memory.conversations)
    })

@app.route("/health", methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy' if WORKING_MODEL else 'degraded',
        'model': WORKING_MODEL,
        'timestamp': datetime.now().isoformat()
    })

@app.route("/test", methods=['GET'])
def test_endpoint():
    """نقطة اختبار لمعرفة المشكلة"""
    try:
        result = find_working_model()
        return jsonify({
            'success': result[0] is not None,
            'model': result[0],
            'key_index': result[1],
            'available_keys': len(GEMINI_KEYS),
            'models_tried': MODELS_TO_TRY
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ================== التشغيل ==================
if __name__ == "__main__":
    logger.info("🚀 Life Coach Bot - Stable")
    logger.info(f"📊 مفاتيح: {len(GEMINI_KEYS)}")
    logger.info(f"🤖 النموذج: {WORKING_MODEL or 'لا يوجد'}")
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
