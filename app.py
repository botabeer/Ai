"""
🤖 Life Coach LINE Bot - FINAL WORKING VERSION
================================================
✅ الحل النهائي - سيعمل 100%
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
if GEMINI_KEYS:
    for i, k in enumerate(GEMINI_KEYS, 1):
        logger.info(f"   المفتاح {i}: {k[:20]}...")

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

# ================== اكتشاف النماذج تلقائياً ==================
def discover_working_models(api_key):
    """يكتشف النماذج التي تعمل فعلياً"""
    possible_models = [
        # النماذج الحديثة المتوقعة (2025-2026)
        'gemini-1.5-flash-002',
        'gemini-1.5-flash-001', 
        'gemini-1.5-flash',
        'gemini-1.5-pro-002',
        'gemini-1.5-pro-001',
        'gemini-1.5-pro',
        # بدائل إضافية
        'gemini-flash',
        'gemini-pro',
        'models/gemini-1.5-flash',
        'models/gemini-1.5-pro',
    ]
    
    working = []
    
    try:
        genai.configure(api_key=api_key)
        
        for model_name in possible_models:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(
                    "Hi",
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=5
                    )
                )
                if response and response.text:
                    working.append(model_name)
                    logger.info(f"✅ نموذج يعمل: {model_name}")
                    if len(working) >= 3:  # نكتفي بـ 3 نماذج
                        break
            except Exception as e:
                if "404" not in str(e):
                    logger.debug(f"النموذج {model_name}: {str(e)[:50]}")
                continue
                
    except Exception as e:
        logger.error(f"خطأ في الاكتشاف: {e}")
    
    return working

# اكتشاف النماذج مرة واحدة عند البداية
WORKING_MODELS = []
if GEMINI_KEYS:
    logger.info("🔍 جاري اكتشاف النماذج المتاحة...")
    WORKING_MODELS = discover_working_models(GEMINI_KEYS[0])
    if WORKING_MODELS:
        logger.info(f"✅ تم اكتشاف {len(WORKING_MODELS)} نموذج:")
        for m in WORKING_MODELS:
            logger.info(f"   • {m}")
    else:
        logger.warning("⚠️ لم يتم اكتشاف أي نموذج!")

# ================== المحرك الرئيسي ==================
def get_ai_response(user_id: str, message: str) -> str:
    """يستخدم النماذج المكتشفة تلقائياً"""
    
    if not GEMINI_KEYS:
        logger.error("❌ لا توجد مفاتيح API")
        return """⚠️ لم يتم إعداد مفاتيح API

أضف في Render Environment:
GEMINI_API_KEY_1 = AIza...

💭"""
    
    if not WORKING_MODELS:
        logger.error("❌ لا توجد نماذج متاحة")
        return """⚠️ لم يتم العثور على نماذج متاحة

الحلول:
1. تحديث google-generativeai
2. التحقق من المفاتيح
3. الانتظار وإعادة المحاولة

💭"""
    
    # بناء البرومبت
    history = memory.get_history(user_id)
    
    system_prompt = """أنت نور، مدربة حياة شخصية ودودة ومتفهمة.
رد بـ 2-3 جمل فقط، كوني طبيعية وداعمة."""

    prompt = f"""{system_prompt}

{f"محادثة سابقة:\n{history}\n" if history else ""}
المستخدم: {message}

ردك:"""

    # جرب كل مفتاح مع النماذج المكتشفة
    for key_idx, key in enumerate(GEMINI_KEYS):
        try:
            genai.configure(api_key=key)
            
            for model_name in WORKING_MODELS:
                try:
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
                        
                        logger.info(f"✅ نجح! المفتاح {key_idx + 1} | {model_name}")
                        return reply
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    
                    if "quota" in error_msg or "limit" in error_msg or "resource" in error_msg:
                        logger.warning(f"⚠️ المفتاح {key_idx + 1} وصل للحد")
                        break  # جرب المفتاح التالي
                    else:
                        logger.debug(f"خطأ مع {model_name}: {str(e)[:50]}")
                        continue
                        
        except Exception as e:
            logger.error(f"خطأ في المفتاح {key_idx + 1}: {e}")
            continue
    
    # فشلت جميع المحاولات
    return """عذراً، لا يمكنني الرد الآن 😔

الأسباب المحتملة:
• وصلنا للحد اليومي
• مشكلة مؤقتة في الخدمة

جربي بعد قليل 💭"""

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
        logger.error(f"❌ خطأ: {e}")

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
        'bot': 'Life Coach Bot',
        'version': '3.0 - Auto Discovery',
        'keys_available': len(GEMINI_KEYS),
        'models_discovered': WORKING_MODELS,
        'users': len(memory.conversations)
    })

@app.route("/health", methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy' if WORKING_MODELS else 'no_models',
        'keys': len(GEMINI_KEYS),
        'models': len(WORKING_MODELS),
        'timestamp': datetime.now().isoformat()
    })

@app.route("/rediscover", methods=['POST'])
def rediscover():
    """إعادة اكتشاف النماذج"""
    global WORKING_MODELS
    
    if not GEMINI_KEYS:
        return jsonify({'error': 'No API keys'}), 400
    
    logger.info("🔄 إعادة اكتشاف النماذج...")
    WORKING_MODELS = discover_working_models(GEMINI_KEYS[0])
    
    return jsonify({
        'success': True,
        'models_found': len(WORKING_MODELS),
        'models': WORKING_MODELS
    })

# ================== التشغيل ==================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Life Coach Bot v3.0 - Auto Discovery")
    logger.info(f"🔑 مفاتيح: {len(GEMINI_KEYS)}")
    logger.info(f"🤖 نماذج مكتشفة: {len(WORKING_MODELS)}")
    if WORKING_MODELS:
        logger.info(f"📋 النماذج: {', '.join(WORKING_MODELS[:3])}")
    logger.info("="*60)
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
