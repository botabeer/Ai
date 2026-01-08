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
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================== Config ==================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini Keys
GEMINI_KEYS = [
    os.getenv('GEMINI_API_KEY_1'),
    os.getenv('GEMINI_API_KEY_2'),
    os.getenv('GEMINI_API_KEY_3')
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k and not k.startswith('your_')]

logger.info(f"🔑 Keys: {len(GEMINI_KEYS)}")

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
    
    # Models to try
    models = ['gemini-1.5-flash-002', 'gemini-1.5-flash', 'gemini-pro']
    
    history = memory.get(user_id)
    prompt = f"""أنت نور، مدربة حياة ودودة. رد بـ 2-3 جمل.

{f"محادثة سابقة:\n{history}\n" if history else ""}
المستخدم: {message}

ردك:"""

    # Try all keys and models
    for key in GEMINI_KEYS:
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
                        logger.info(f"✅ Success")
                        return reply
                
                except Exception as e:
                    if "quota" in str(e).lower() or "limit" in str(e).lower():
                        break  # Try next key
                    continue
        
        except:
            continue
    
    return "عذراً، لا يمكنني الرد الآن 😔\nجربي بعد قليل 💭"

# ================== LINE Handlers ==================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_id = event.source.user_id
        message = event.message.text.strip()
        
        logger.info(f"📨 {message[:40]}")
        
        reply = get_reply(user_id, message)
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
    except Exception as e:
        logger.error(f"❌ {e}")

@handler.add(FollowEvent)
def handle_follow(event):
    welcome = "مرحباً بك! أنا نور 🌟\n\nمدربتك الشخصية هنا لدعمك.\nشاركيني ما في بالك 💭"
    
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
        logger.error(f"❌ {e}")

# ================== Health Endpoints ==================
@app.route("/")
def home():
    return jsonify({'status': 'ok', 'bot': 'running'}), 200

@app.route("/health")
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route("/ping")
def ping():
    return "pong", 200

# ================== Run ==================
if __name__ == "__main__":
    logger.info("🚀 Bot Starting...")
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
