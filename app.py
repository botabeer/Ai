"""
🤖 Life Coach LINE Bot - FIXED VERSION
=========================================
✅ أسماء النماذج الصحيحة لـ 2026
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
from datetime import datetime
from collections import defaultdict, deque
import logging

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

logger.info(f"🔑 مفاتيح متاحة: {len(GEMINI_KEYS)}")

# ================== الذاكرة ==================
class SimpleMemory:
    def __init__(self):
        self.conversations = defaultdict(lambda: deque(maxlen=4))
        
    def add_message(self, user_id: str, role: str, content: str):
        self.conversations[user_id].append({
            'role': role,
            'content': content[:80]
        })
        
    def get_history(self, user_id: str) -> str:
        history = list(self.conversations[user_id])[-2:]
        if not history:
            return ""
        
        result = []
        for msg in history:
            role = "المستخدم" if msg['role'] == 'user' else "نور"
            result.append(f"{role}: {msg['content']}")
        return "\n".join(result)

memory = SimpleMemory()

# ================== النماذج الصحيحة لـ 2026 ==================
# ⚠️ هذه الأسماء الصحيحة المدعومة حالياً
MODELS = [
    'gemini-1.5-flash-002',      # الأحدث والأسرع
    'gemini-1.5-flash-001',      # بديل مستقر
    'gemini-1.5-flash',          # النسخة العامة
    'gemini-1.5-pro-002',        # الأقوى
    'gemini-1.5-pro-001',        # بديل Pro
]

# ================== المحرك الرئيسي ==================
def get_ai_response(user_id: str, message: str) -> str:
    """يحاول جميع المفاتيح والنماذج حتى ينجح"""
    
    if not GEMINI_KEYS:
        return "⚠️ لم يتم إعداد مفاتيح API"
    
    # بناء البرومبت
    history = memory.get_history(user_id)
    
    system_prompt = """أنت نور، مدربة حياة شخصية ودودة ومتفهمة.
رد بـ 2-3 جمل فقط، كوني طبيعية وداعمة."""

    prompt = f"""{system_prompt}

{f"محادثة سابقة:\n{history}\n" if history else ""}
المستخدم: {message}

ردك:"""

    # جرب كل مفتاح مع كل نموذج
    last_error = None
    
    for key_idx, key in enumerate(GEMINI_KEYS):
        logger.info(f"🔑 جرب المفتاح {key_idx + 1}/{len(GEMINI_KEYS)}")
        
        try:
            genai.configure(api_key=key)
        except Exception as e:
            logger.error(f"❌ خطأ في configure للمفتاح {key_idx + 1}: {e}")
            continue
        
        for model_name in MODELS:
            try:
                logger.info(f"  🤖 جرب النموذج: {model_name}")
                
                model = genai.GenerativeModel(model_name)
                
                response = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.9,
                        top_p=0.95,
                        max_output_tokens=150,
                    )
                )
                
                if response and response.text:
                    reply = response.text.strip()
                    
                    # حفظ في الذاكرة
                    memory.add_message(user_id, 'user', message)
                    memory.add_message(user_id, 'assistant', reply)
                    
                    logger.info(f"✅ نجح! المفتاح {key_idx + 1} | النموذج: {model_name}")
                    return reply
                
            except Exception as e:
                error_msg = str(e).lower()
                last_error = str(e)
                
                # لو 404 = النموذج مش موجود، جرب التالي
                if "404" in error_msg or "not found" in error_msg:
                    logger.info(f"  ⏭️ النموذج {model_name} غير متوفر")
                    continue
                
                # لو quota = المفتاح وصل للحد
                elif "quota" in error_msg or "limit" in error_msg or "resource" in error_msg:
                    logger.warning(f"  ⚠️ المفتاح {key_idx + 1} وصل للحد اليومي")
                    break  # جرب المفتاح التالي
                
                # أي خطأ آخر
                else:
                    logger.error(f"  ❌ خطأ: {str(e)[:100]}")
                    continue
    
    # إذا وصلنا هنا، كل المحاولات فشلت
    logger.error(f"❌ فشلت جميع المحاولات. آخر خطأ: {last_error}")
    
    # رسائل مخصصة حسب نوع الخطأ
    if last_error and ("quota" in last_error.lower() or "limit" in last_error.lower()):
        return """عذراً، وصلنا للحد اليومي 📊

حلول:
1. جربي بعد 24 ساعة
2. أو اطلبي من المطور إضافة مفاتيح

شكراً لتفهمك! 🌸"""
    
    elif last_error and ("404" in last_error or "not found" in last_error):
        return """النماذج تحتاج تحديث 🔄

المطور يحتاج:
1. تحديث google-generativeai
2. استخدام أسماء النماذج الجديدة

جربي لاحقاً 💭"""
    
    else:
        return """عذراً، حصل خطأ تقني 🔧

جربي:
1. أرسلي الرسالة مرة ثانية
2. إذا استمر، راجعي المطور

آسفة! 🌸"""

# ================== معالجات LINE ==================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("❌ Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"❌ Callback error: {e}")
        abort(500)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_id = event.source.user_id
        message = event.message.text.strip()
        
        logger.info(f"📨 [{user_id[:8]}]: {message[:40]}")
        
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
        logger.error(f"❌ خطأ في handle_message: {e}")
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="عذراً، خطأ مؤقت 🔧")]
                    )
                )
        except:
            pass

@handler.add(FollowEvent)
def handle_follow(event):
    welcome = """مرحباً بك! أنا نور 🌟

مدربتك الشخصية هنا لدعمك.
شاركيني ما في بالك 💭"""
    
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
        logger.error(f"❌ خطأ في الترحيب: {e}")

# ================== نقاط النهاية ==================
@app.route("/", methods=['GET'])
def home():
    return jsonify({
        'status': 'running',
        'bot': 'Life Coach Bot - FIXED',
        'version': '2.0',
        'available_keys': len(GEMINI_KEYS),
        'models': MODELS,
        'users': len(memory.conversations)
    })

@app.route("/health", methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'keys_available': len(GEMINI_KEYS),
        'models_count': len(MODELS),
        'timestamp': datetime.now().isoformat()
    })

@app.route("/test", methods=['GET'])
def test_models():
    """اختبار شامل لجميع المفاتيح والنماذج"""
    
    if not GEMINI_KEYS:
        return jsonify({
            'success': False,
            'error': 'No API keys configured'
        }), 500
    
    results = {
        'total_keys': len(GEMINI_KEYS),
        'total_models': len(MODELS),
        'tests': []
    }
    
    for key_idx, key in enumerate(GEMINI_KEYS):
        key_result = {
            'key_index': key_idx + 1,
            'key_prefix': key[:15] + '...',
            'models': []
        }
        
        try:
            genai.configure(api_key=key)
            
            for model_name in MODELS:
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(
                        "Say hi in Arabic in 3 words",
                        generation_config=genai.types.GenerationConfig(
                            max_output_tokens=10
                        )
                    )
                    
                    key_result['models'].append({
                        'name': model_name,
                        'status': 'working',
                        'response': response.text[:50] if response.text else 'empty'
                    })
                    
                except Exception as e:
                    error_type = 'quota' if 'quota' in str(e).lower() or 'limit' in str(e).lower() else \
                                 '404' if '404' in str(e) else 'error'
                    
                    key_result['models'].append({
                        'name': model_name,
                        'status': error_type,
                        'error': str(e)[:100]
                    })
                    
        except Exception as e:
            key_result['error'] = str(e)[:100]
        
        results['tests'].append(key_result)
    
    # إيجاد أول مفتاح ونموذج يعملان
    working_combo = None
    for test in results['tests']:
        for model_test in test.get('models', []):
            if model_test['status'] == 'working':
                working_combo = {
                    'key': test['key_index'],
                    'model': model_test['name']
                }
                break
        if working_combo:
            break
    
    results['working_combination'] = working_combo
    results['success'] = working_combo is not None
    
    return jsonify(results)

# ================== التشغيل ==================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Life Coach Bot - FIXED VERSION 2.0")
    logger.info(f"🔑 مفاتيح API: {len(GEMINI_KEYS)}")
    logger.info(f"🤖 نماذج: {MODELS}")
    logger.info("="*60)
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
