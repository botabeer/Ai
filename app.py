"""
🤖 Life Coach LINE Bot - Groq Cloud Version
============================================
✅ بديل مجاني 100% - 1000 طلب يوميًا
"""

from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
from groq import Groq
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

# Groq API Key
GROQ_API_KEY = os.getenv('GROQ_API_KEY')

if not GROQ_API_KEY or GROQ_API_KEY.startswith('your_'):
    logger.error("❌ No valid Groq API key!")
    raise ValueError("Groq API key is required")

groq_client = Groq(api_key=GROQ_API_KEY)
logger.info(f"🔑 Groq API key configured")

# ================== Memory ==================
class Memory:
    def __init__(self):
        self.chats = defaultdict(lambda: deque(maxlen=4))
    
    def add(self, user_id, role, msg):
        self.chats[user_id].append({'role': role, 'content': msg[:100]})
    
    def get(self, user_id):
        return list(self.chats[user_id])[-2:]

memory = Memory()

# ================== AI Response ==================
def get_reply(user_id, message):
    """توليد الرد باستخدام Groq"""
    
    try:
        # بناء المحادثة
        messages = [
            {
                "role": "system",
                "content": "أنت نور، مدربة حياة ودودة وداعمة. رد بـ 2-3 جمل بأسلوب دافئ ومتفهم."
            }
        ]
        
        # إضافة السياق من الذاكرة
        history = memory.get(user_id)
        for msg in history:
            messages.append(msg)
        
        # إضافة الرسالة الحالية
        messages.append({
            "role": "user",
            "content": message
        })
        
        logger.info(f"🤖 Generating response using Groq...")
        
        # استدعاء Groq API
        chat_completion = groq_client.chat.completions.create(
            messages=messages,
            model="llama-3.3-70b-versatile",  # موديل قوي ومجاني
            temperature=0.9,
            max_tokens=150,
            top_p=1
        )
        
        reply = chat_completion.choices[0].message.content.strip()
        
        # حفظ في الذاكرة
        memory.add(user_id, 'user', message)
        memory.add(user_id, 'assistant', reply)
        
        logger.info(f"✅ Response generated successfully")
        return reply
        
    except Exception as e:
        logger.error(f"❌ Groq API error: {e}")
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
        'version': '2.0 - Groq',
        'provider': 'Groq Cloud'
    }), 200

@app.route("/health")
def health():
    return jsonify({
        'status': 'healthy',
        'provider': 'groq'
    }), 200

@app.route("/ping")
def ping():
    return "pong", 200

# ================== Startup ==================
logger.info("="*60)
logger.info("🚀 Life Coach Bot Starting...")
logger.info(f"🤖 Using Groq Cloud (Free Tier)")
logger.info(f"✅ LINE Config: OK")
logger.info("="*60)

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    logger.info(f"🏃 Running in development mode on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
