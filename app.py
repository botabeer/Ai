"""
🤖 Life Coach LINE Bot - Groq Version
=====================================
✅ مجاني 100% - 1000 طلب يوميًا
✅ محسّن وجاهز للإنتاج
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
from datetime import datetime

# ================== Logging Setup ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================== Configuration ==================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')

# التحقق من المتغيرات
if not LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_ACCESS_TOKEN.startswith('your_'):
    logger.error("❌ LINE_CHANNEL_ACCESS_TOKEN مفقود!")
    raise ValueError("LINE credentials not found")

if not LINE_CHANNEL_SECRET or LINE_CHANNEL_SECRET.startswith('your_'):
    logger.error("❌ LINE_CHANNEL_SECRET مفقود!")
    raise ValueError("LINE credentials not found")

if not GROQ_API_KEY or GROQ_API_KEY.startswith('your_'):
    logger.error("❌ GROQ_API_KEY مفقود!")
    raise ValueError("Groq API key is required")

# إعداد LINE
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# إعداد Groq
groq_client = Groq(api_key=GROQ_API_KEY)

logger.info("✅ جميع الإعدادات جاهزة")

# ================== Memory System ==================
class ConversationMemory:
    """نظام ذاكرة محسّن للمحادثات"""
    
    def __init__(self, max_history=6):
        self.conversations = defaultdict(lambda: deque(maxlen=max_history))
        self.user_info = {}
    
    def add_message(self, user_id, role, content):
        """إضافة رسالة للذاكرة"""
        self.conversations[user_id].append({
            'role': role,
            'content': content[:200],  # تقليص النص الطويل
            'timestamp': datetime.now().isoformat()
        })
    
    def get_history(self, user_id, limit=4):
        """جلب آخر رسائل من المحادثة"""
        history = list(self.conversations[user_id])
        return history[-limit:] if len(history) > limit else history
    
    def clear_user(self, user_id):
        """مسح محادثات مستخدم معين"""
        if user_id in self.conversations:
            self.conversations[user_id].clear()
            logger.info(f"🗑️ تم مسح ذاكرة المستخدم: {user_id[:8]}...")

memory = ConversationMemory()

# ================== AI Response Generator ==================
def generate_ai_response(user_id, message):
    """
    توليد رد ذكي باستخدام Groq
    
    Args:
        user_id: معرّف المستخدم
        message: رسالة المستخدم
    
    Returns:
        str: الرد المولّد
    """
    try:
        # بناء رسائل المحادثة
        messages = [
            {
                "role": "system",
                "content": """أنت نور، مدربة حياة متخصصة وداعمة.

خصائصك:
- دافئة ومتعاطفة ومستمعة جيدة
- تعطي نصائح عملية وواقعية
- تستخدم لغة عربية بسيطة وودية
- ردودك قصيرة (2-3 جمل) وواضحة
- تشجع على التفكير الإيجابي والنمو الشخصي
- تستخدم الإيموجي بشكل معتدل ومناسب

أسلوبك:
- استمعي للمشاعر وأظهري التفهم
- اسألي أسئلة تساعد على التأمل
- قدمي خطوات عملية صغيرة
- كوني إيجابية لكن واقعية"""
            }
        ]
        
        # إضافة سياق المحادثة السابقة
        history = memory.get_history(user_id, limit=4)
        for msg in history:
            messages.append({
                'role': msg['role'],
                'content': msg['content']
            })
        
        # إضافة الرسالة الحالية
        messages.append({
            "role": "user",
            "content": message
        })
        
        logger.info(f"🤖 توليد رد للمستخدم {user_id[:8]}...")
        
        # استدعاء Groq API
        chat_completion = groq_client.chat.completions.create(
            messages=messages,
            model="llama-3.3-70b-versatile",
            temperature=0.8,
            max_tokens=200,
            top_p=0.9,
            stream=False
        )
        
        reply = chat_completion.choices[0].message.content.strip()
        
        # حفظ في الذاكرة
        memory.add_message(user_id, 'user', message)
        memory.add_message(user_id, 'assistant', reply)
        
        logger.info(f"✅ تم توليد الرد بنجاح")
        return reply
        
    except Exception as e:
        logger.error(f"❌ خطأ في Groq API: {str(e)}")
        
        # رسالة خطأ ودية
        error_messages = [
            "عذراً، واجهت مشكلة صغيرة 😔\nجربي مرة أخرى بعد قليل 💭",
            "آسفة، لا أستطيع الرد الآن 🙏\nلكن أنا هنا عندما تحتاجيني ✨",
            "حدث خطأ مؤقت 😊\nحاولي مرة أخرى بعد لحظات 🌟"
        ]
        
        import random
        return random.choice(error_messages)

# ================== LINE Webhook Handler ==================
@app.route("/callback", methods=['POST'])
def callback():
    """معالج webhook من LINE"""
    
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    logger.info("📨 استلام طلب webhook")
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("❌ توقيع غير صالح")
        abort(400)
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة webhook: {e}")
        abort(500)
    
    return 'OK'

# ================== Message Handler ==================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """معالج الرسائل النصية"""
    
    try:
        user_id = event.source.user_id
        message = event.message.text.strip()
        
        logger.info(f"📩 رسالة من {user_id[:8]}...: {message[:50]}")
        
        # أوامر خاصة
        if message.lower() in ['مسح', 'clear', 'reset']:
            memory.clear_user(user_id)
            reply = "تم مسح المحادثة 🔄\nلنبدأ من جديد! كيف يمكنني مساعدتك؟ 😊"
        else:
            # توليد الرد العادي
            reply = generate_ai_response(user_id, message)
        
        logger.info(f"💬 الرد: {reply[:50]}...")
        
        # إرسال الرد
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
        
        logger.info("✅ تم إرسال الرد بنجاح")
        
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة الرسالة: {e}", exc_info=True)

# ================== Follow Event Handler ==================
@handler.add(FollowEvent)
def handle_follow(event):
    """معالج إضافة صديق جديد"""
    
    welcome_message = """مرحباً بك! 🌟

أنا نور، مدربتك الشخصية 💫

أنا هنا لأستمع لك وأدعمك في رحلتك.
شاركيني ما في بالك، أنا موجودة لأجلك 💙

💡 لمسح المحادثة: اكتبي "مسح" """
    
    try:
        user_id = event.source.user_id
        logger.info(f"👋 مستخدم جديد: {user_id[:8]}...")
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=welcome_message)]
                )
            )
        
        logger.info("✅ تم إرسال رسالة الترحيب")
        
    except Exception as e:
        logger.error(f"❌ خطأ في إرسال الترحيب: {e}")

# ================== Health Check Endpoints ==================
@app.route("/")
def home():
    """الصفحة الرئيسية"""
    return jsonify({
        'status': 'running',
        'bot': 'Life Coach Bot - نور',
        'version': '2.0',
        'provider': 'Groq Cloud',
        'model': 'llama-3.3-70b-versatile',
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route("/health")
def health():
    """فحص صحة الخدمة"""
    return jsonify({
        'status': 'healthy',
        'ai_provider': 'groq',
        'memory_users': len(memory.conversations)
    }), 200

@app.route("/ping")
def ping():
    """اختبار بسيط"""
    return "pong", 200

@app.route("/stats")
def stats():
    """إحصائيات الاستخدام"""
    return jsonify({
        'total_users': len(memory.conversations),
        'total_messages': sum(len(conv) for conv in memory.conversations.values()),
        'active_conversations': len([c for c in memory.conversations.values() if len(c) > 0])
    }), 200

# ================== Startup ==================
if __name__ == "__main__":
    logger.info("="*70)
    logger.info("🚀 Life Coach Bot - نور")
    logger.info("="*70)
    logger.info(f"🤖 AI Provider: Groq Cloud")
    logger.info(f"📦 Model: llama-3.3-70b-versatile")
    logger.info(f"✅ LINE Bot: Configured")
    logger.info(f"💾 Memory: Active")
    logger.info("="*70)
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
