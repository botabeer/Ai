"""
🤖 Life Coach LINE Bot - Ultra Simple & Stable
===============================================
✅ نسخة محدثة لـ Gemini 2026 Models
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
from collections import defaultdict, deque
import logging

# ================== Setup ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================== Config ==================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    logger.error("❌ Missing LINE credentials!")
    raise ValueError("LINE credentials not found in environment")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini Keys
GEMINI_KEYS = [
    os.getenv('GEMINI_API_KEY_1'),
    os.getenv('GEMINI_API_KEY_2'),
    os.getenv('GEMINI_API_KEY_3')
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k and not k.startswith('your_')]

if not GEMINI_KEYS:
    logger.error("❌ No valid Gemini API keys!")
    raise ValueError("At least one Gemini API key is required")

logger.info(f"🔑 Loaded {len(GEMINI_KEYS)} Gemini API key(s)")

# ================== Memory ==================
class Memory:
    def __init__(self):
        self.chats = defaultdict(lambda: deque(maxlen=4))
    
    def add(self, user_id, role, msg):
        self.chats[user_id].append({'role': role, 'msg': msg[:80]})
    
    def get(self, user_id):
        h = list(self.chats[user_id])[-2:]
        if not h:
            return ""
        return "\n".join([f"{'المستخدم' if m['role']=='user' else 'نور'}: {m['msg']}" for m in h])

memory = Memory()

# ================== AI Response ==================
def get_reply(user_id, message):
    """توليد الرد"""
    
    if not GEMINI_KEYS:
        return "⚠️ البوت غير مهيأ"
    
    # ✅ الأسماء الصحيحة لنماذج Gemini 2026
    # من الأسرع والأرخص إلى الأقوى
    models = [
        'gemini-2.0-flash-exp',      # أسرع وأرخص (تجريبي)
        'gemini-2.0-flash',           # سريع ومستقر
        'gemini-1.5-flash-latest',    # متوفر للجميع
        'gemini-1.5-pro-latest',      # أقوى لكن أبطأ
    ]
    
    history = memory.get(user_id)
    prompt = f"""أنت نور، مدربة حياة ودودة. رد بـ 2-3 جمل.

{f"محادثة سابقة:\n{history}\n" if history else ""}
المستخدم: {message}

ردك:"""

    last_error = None
    
    # Try all keys and models
    for key_idx, key in enumerate(GEMINI_KEYS):
        logger.info(f"🔑 Trying key #{key_idx+1}")
        
        try:
            genai.configure(api_key=key)
            
            for model_name in models:
                try:
                    logger.info(f"🤖 Trying model: {model_name}")
                    
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(
                        prompt,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.9,
                            max_output_tokens=150
                        )
                    )
                    
                    if response and response.text:
                        reply = response.text.strip()
                        memory.add(user_id, 'user', message)
                        memory.add(user_id, 'assistant', reply)
                        logger.info(f"✅ SUCCESS with key #{key_idx+1}, model: {model_name}")
                        return reply
                
                except Exception as model_error:
                    error_str = str(model_error)
                    error_lower = error_str.lower()
                    
                    # Log the ACTUAL error
                    logger.error(f"❌ Key #{key_idx+1}, Model {model_name} FAILED:")
                    logger.error(f"   Error: {error_str[:200]}")
                    
                    last_error = error_str
                    
                    # Check if quota/limit issue
                    if any(x in error_lower for x in ["quota", "limit", "resource", "exhausted"]):
                        logger.warning(f"⚠️ Key #{key_idx+1} QUOTA EXCEEDED, trying next key...")
                        break  # Try next key
                    
                    # Check if invalid API key
                    if any(x in error_lower for x in ["invalid", "api_key", "unauthorized", "403"]):
                        logger.error(f"🚫 Key #{key_idx+1} is INVALID!")
                        break  # Try next key
                    
                    # Check if model not found
                    if "404" in error_lower or "not found" in error_lower:
                        logger.warning(f"⚠️ Model {model_name} not available, trying next model...")
                        continue  # Try next model
                    
                    # Unknown error, try next model
                    continue
        
        except Exception as key_error:
            logger.error(f"❌ Key #{key_idx+1} CONFIGURATION FAILED: {key_error}")
            continue
    
    # All attempts failed
    logger.error("="*60)
    logger.error("❌ ALL KEYS AND MODELS EXHAUSTED!")
    if last_error:
        logger.error(f"Last error: {last_error[:300]}")
    logger.error("="*60)
    
    return "عذراً، لا يمكنني الرد الآن 😔\nجربي بعد قليل 💭"

# ================== LINE Handlers ==================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    logger.info(f"📨 Received webhook request")
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("❌ Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        abort(500)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_id = event.source.user_id
        message = event.message.text.strip()
        
        logger.info(f"📩 Message from {user_id[:8]}...: {message[:40]}")
        
        # Generate reply
        reply = get_reply(user_id, message)
        logger.info(f"💬 Reply: {reply[:40]}")
        
        # Send reply
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
        
        logger.info("✅ Reply sent successfully")
        
    except Exception as e:
        logger.error(f"❌ Error handling message: {e}", exc_info=True)

@handler.add(FollowEvent)
def handle_follow(event):
    welcome = "مرحباً بك! أنا نور 🌟\n\nمدربتك الشخصية هنا لدعمك.\nشاركيني ما في بالك 💭"
    
    try:
        logger.info(f"👋 New follower: {event.source.user_id[:8]}...")
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=welcome)]
                )
            )
        
        logger.info("✅ Welcome message sent")
        
    except Exception as e:
        logger.error(f"❌ Error sending welcome: {e}")

# ================== Health Endpoints ==================
@app.route("/")
def home():
    return jsonify({
        'status': 'ok',
        'bot': 'Life Coach Bot',
        'version': '2.0'
    }), 200

@app.route("/health")
def health():
    return jsonify({
        'status': 'healthy',
        'gemini_keys': len(GEMINI_KEYS)
    }), 200

@app.route("/ping")
def ping():
    return "pong", 200

# ================== Test Endpoints ==================
@app.route("/test-gemini")
def test_gemini():
    """اختبار سريع لمفاتيح Gemini"""
    results = []
    
    test_models = [
        'gemini-2.0-flash-exp',
        'gemini-2.0-flash',
        'gemini-1.5-flash-latest',
        'gemini-1.5-pro-latest'
    ]
    
    for idx, key in enumerate(GEMINI_KEYS):
        key_result = {'key': f"Key #{idx+1}", 'models': []}
        
        try:
            genai.configure(api_key=key)
            
            for model_name in test_models:
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(
                        "مرحبا",
                        generation_config=genai.types.GenerationConfig(
                            max_output_tokens=10
                        )
                    )
                    
                    key_result['models'].append({
                        'name': model_name,
                        'status': '✅ working',
                        'sample': response.text[:30]
                    })
                    
                except Exception as e:
                    key_result['models'].append({
                        'name': model_name,
                        'status': '❌ failed',
                        'error': str(e)[:100]
                    })
            
            results.append(key_result)
            
        except Exception as e:
            results.append({
                'key': f"Key #{idx+1}",
                'status': 'failed',
                'error': str(e)[:100]
            })
    
    return jsonify(results), 200

@app.route("/list-models")
def list_models():
    """عرض جميع النماذج المتاحة"""
    if not GEMINI_KEYS:
        return jsonify({'error': 'No API keys'}), 400
    
    try:
        genai.configure(api_key=GEMINI_KEYS[0])
        models = genai.list_models()
        
        available = []
        for m in models:
            if 'generateContent' in m.supported_generation_methods:
                available.append({
                    'name': m.name,
                    'display_name': m.display_name,
                    'description': m.description
                })
        
        return jsonify({
            'total': len(available),
            'models': available
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ================== Startup ==================
logger.info("="*60)
logger.info("🚀 Life Coach Bot Starting...")
logger.info(f"🔑 Gemini Keys: {len(GEMINI_KEYS)}")
logger.info(f"✅ LINE Config: OK")
logger.info(f"📅 Using 2026 Gemini Models")
logger.info("="*60)

# ⚠️ هذا الجزء فقط للتطوير المحلي
# على Render سيتم تشغيل التطبيق بواسطة gunicorn من Procfile
if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    logger.info(f"🏃 Running in development mode on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
