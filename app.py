"""
🤖 Life Coach LINE Bot - Ultra Simple & Stable
===============================================
✅ نسخة مبسطة تعمل 100% على Render
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
    
    # Models to try in order of preference
    models = ['gemini-1.5-flash-002', 'gemini-1.5-flash', 'gemini-pro']
    
    history = memory.get(user_id)
    prompt = f"""أنت نور، مدربة حياة ودودة. رد بـ 2-3 جمل.

{f"محادثة سابقة:\n{history}\n" if history else ""}
المستخدم: {message}

ردك:"""

    # Try all keys and models
    for key_idx, key in enumerate(GEMINI_KEYS):
        try:
            genai.configure(api_key=key)
            
            for model_name in models:
                try:
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
                        logger.info(f"✅ Response generated using key {key_idx+1}, model {model_name}")
                        return reply
                
                except Exception as model_error:
                    error_str = str(model_error).lower()
                    if "quota" in error_str or "limit" in error_str or "resource" in error_str:
                        logger.warning(f"⚠️ Key {key_idx+1} quota exceeded, trying next key...")
                        break  # Try next key
                    logger.debug(f"Model {model_name} failed: {model_error}")
                    continue  # Try next model
        
        except Exception as key_error:
            logger.error(f"❌ Key {key_idx+1} failed: {key_error}")
            continue
    
    logger.error("❌ All keys and models exhausted")
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
        'version': '1.0'
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

# ================== Startup ==================
logger.info("="*60)
logger.info("🚀 Life Coach Bot Starting...")
logger.info(f"🔑 Gemini Keys: {len(GEMINI_KEYS)}")
logger.info(f"✅ LINE Config: OK")
logger.info("="*60)

# ⚠️ هذا الجزء فقط للتطوير المحلي
# على Render سيتم تشغيل التطبيق بواسطة gunicorn من Procfile
if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    logger.info(f"🏃 Running in development mode on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
