"""
🤖 Life Coach LINE Bot - Render Optimized Version
==================================================
✅ يعمل بشكل مستقر على Render بدون إعادة تشغيل
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
import threading

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

# ================== النماذج المتاحة ==================
WORKING_MODELS = []
MODELS_DISCOVERED = False

def discover_models_async():
    """اكتشاف النماذج في خلفية لتسريع Startup"""
    global WORKING_MODELS, MODELS_DISCOVERED
    
    if not GEMINI_KEYS:
        logger.warning("⚠️ لا توجد مفاتيح API")
        MODELS_DISCOVERED = True
        return
    
    logger.info("🔍 بدء اكتشاف النماذج...")
    
    # نماذج مجربة ومعروفة
    models_to_try = [
        'gemini-1.5-flash-002',
        'gemini-1.5-flash',
        'gemini-1.5-pro',
        'gemini-pro'
    ]
    
    try:
        genai.configure(api_key=GEMINI_KEYS[0])
        
        for model_name in models_to_try:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(
                    "Hi",
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=5
                    )
                )
                if response and response.text:
                    WORKING_MODELS.append(model_name)
                    logger.info(f"✅ نموذج يعمل: {model_name}")
                    
                    # نكتفي بنموذج واحد للسرعة
                    if len(WORKING_MODELS) >= 1:
                        break
                        
            except Exception as e:
                if "404" not in str(e):
                    logger.debug(f"❌ {model_name}: {str(e)[:30]}")
                continue
                
    except Exception as e:
        logger.error(f"❌ خطأ في الاكتشاف: {e}")
    
    MODELS_DISCOVERED = True
    logger.info(f"✅ اكتشاف النماذج اكتمل: {len(WORKING_MODELS)} نموذج")

# بدء الاكتشاف في خلفية
discovery_thread = threading.Thread(target=discover_models_async, daemon=True)
discovery_thread.start()

# ================== المحرك الرئيسي ==================
def get_ai_response(user_id: str, message: str) -> str:
    """توليد الرد بذكاء"""
    
    # انتظر اكتمال الاكتشاف (مع timeout)
    timeout = 10
    while not MODELS_DISCOVERED and timeout > 0:
        import time
        time.sleep(0.5)
        timeout -= 0.5
    
    if not GEMINI_KEYS:
        return "⚠️ البوت غير مهيأ. تحقق من المفاتيح."
    
    if not WORKING_MODELS:
        return "⚠️ لا توجد نماذج متاحة حالياً. جربي لاحقاً 💭"
    
    # بناء البرومبت
    history = memory.get_history(user_id)
    
    system_prompt = """أنت نور، مدربة حياة شخصية ودودة.
رد بـ 2-3 جمل فقط، كوني طبيعية وداعمة."""

    prompt = f"""{system_prompt}

{f"محادثة سابقة:\n{history}\n" if history else ""}
المستخدم: {message}

ردك:"""

    # جرب كل مفتاح
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
                        
                        logger.info(f"✅ نجح! المفتاح {key_idx + 1}")
                        return reply
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    
                    if "quota" in error_msg or "limit" in error_msg:
                        logger.warning(f"⚠️ المفتاح {key_idx + 1} وصل للحد")
                        break
                    else:
                        continue
                        
        except Exception as e:
            logger.error(f"خطأ في المفتاح {key_idx + 1}: {str(e)[:50]}")
            continue
    
    return "عذراً، لا يمكنني الرد الآن 😔\nجربي بعد قليل 💭"

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

# ================== نقاط النهاية - CRITICAL FOR RENDER ==================
@app.route("/", methods=['GET'])
def home():
    """الصفحة الرئيسية - Render يفحصها"""
    return jsonify({
        'status': 'ok',
        'bot': 'Life Coach Bot',
        'version': '3.1',
        'ready': MODELS_DISCOVERED,
        'models': len(WORKING_MODELS)
    }), 200

@app.route("/health", methods=['GET'])
def health():
    """Health Check - مهم جداً لـ Render"""
    status_code = 200 if MODELS_DISCOVERED else 503
    
    return jsonify({
        'status': 'healthy' if MODELS_DISCOVERED else 'starting',
        'keys': len(GEMINI_KEYS),
        'models': len(WORKING_MODELS),
        'ready': MODELS_DISCOVERED,
        'timestamp': datetime.now().isoformat()
    }), status_code

@app.route("/ping", methods=['GET'])
def ping():
    """نقطة فحص سريعة"""
    return "pong", 200

# ================== التشغيل ==================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🚀 Life Coach Bot v3.1 - Render Optimized")
    logger.info(f"🔑 مفاتيح: {len(GEMINI_KEYS)}")
    logger.info("⏳ اكتشاف النماذج في الخلفية...")
    logger.info("="*60)
    
    port = int(os.getenv('PORT', 5000))
    
    # إعدادات محسّنة لـ Render
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True  # مهم للـ threading
    )
