"""
🤖 Life Coach LINE Bot - Professional Edition
==============================================
✨ Enterprise-grade chatbot with Groq AI
Version: 3.0.0 - Standalone Edition
"""

from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
from groq import Groq, GroqError
import logging
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from collections import defaultdict, deque
from typing import List, Dict, Optional
from dataclasses import dataclass
import threading
import random
import time

# Load environment variables
load_dotenv()

# ==================== Configuration ====================
@dataclass
class Config:
    """Application configuration"""
    # LINE Bot
    line_access_token: str
    line_channel_secret: str
    
    # Groq AI
    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"
    ai_temperature: float = 0.8
    ai_max_tokens: int = 200
    
    # App Settings
    port: int = 5000
    environment: str = "production"
    log_level: str = "INFO"
    
    # Memory Settings
    max_conversation_history: int = 8
    max_message_length: int = 500
    session_timeout_minutes: int = 30
    
    # Security
    rate_limit_per_minute: int = 10

def load_config() -> Config:
    """Load and validate configuration"""
    return Config(
        line_access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN', ''),
        line_channel_secret=os.getenv('LINE_CHANNEL_SECRET', ''),
        groq_api_key=os.getenv('GROQ_API_KEY', ''),
        groq_model=os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile'),
        ai_temperature=float(os.getenv('AI_TEMPERATURE', 0.8)),
        ai_max_tokens=int(os.getenv('AI_MAX_TOKENS', 200)),
        port=int(os.getenv('PORT', 5000)),
        environment=os.getenv('ENVIRONMENT', 'production'),
        log_level=os.getenv('LOG_LEVEL', 'INFO'),
        max_conversation_history=int(os.getenv('MAX_CONVERSATION_HISTORY', 8)),
        max_message_length=int(os.getenv('MAX_MESSAGE_LENGTH', 500)),
        session_timeout_minutes=int(os.getenv('SESSION_TIMEOUT_MINUTES', 30)),
        rate_limit_per_minute=int(os.getenv('RATE_LIMIT_PER_MINUTE', 10))
    )

config = load_config()

# ==================== Logging Setup ====================
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format='%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Reduce noise from libraries
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ==================== Memory System ====================
@dataclass
class Message:
    """Single message structure"""
    role: str
    content: str
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, str]:
        return {'role': self.role, 'content': self.content}

class ConversationMemory:
    """Thread-safe conversation memory management"""
    
    def __init__(self, max_history: int = 8, max_message_length: int = 500, 
                 session_timeout_minutes: int = 30):
        self.max_history = max_history
        self.max_message_length = max_message_length
        self.session_timeout = timedelta(minutes=session_timeout_minutes)
        
        self._lock = threading.Lock()
        self._conversations: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_history)
        )
        self._user_stats: Dict[str, Dict] = defaultdict(
            lambda: {
                'first_seen': datetime.now(),
                'last_seen': datetime.now(),
                'total_messages': 0,
                'conversations_reset': 0
            }
        )
        
        logger.info(f"💾 Memory initialized: max_history={max_history}")
    
    def _truncate_content(self, content: str) -> str:
        if len(content) <= self.max_message_length:
            return content
        return content[:self.max_message_length] + "..."
    
    def add_message(self, user_id: str, role: str, content: str) -> None:
        with self._lock:
            truncated = self._truncate_content(content)
            message = Message(role=role, content=truncated, timestamp=datetime.now())
            self._conversations[user_id].append(message)
            self._user_stats[user_id]['last_seen'] = datetime.now()
            self._user_stats[user_id]['total_messages'] += 1
    
    def get_history(self, user_id: str, limit: Optional[int] = None) -> List[Dict[str, str]]:
        with self._lock:
            history = list(self._conversations[user_id])
            
            if history and self._is_session_expired(user_id):
                logger.info(f"⏰ Session expired for user {user_id[:8]}...")
                self._conversations[user_id].clear()
                return []
            
            if limit:
                history = history[-limit:]
            
            return [msg.to_dict() for msg in history]
    
    def _is_session_expired(self, user_id: str) -> bool:
        last_seen = self._user_stats[user_id]['last_seen']
        return datetime.now() - last_seen > self.session_timeout
    
    def clear_user(self, user_id: str) -> int:
        with self._lock:
            count = len(self._conversations[user_id])
            self._conversations[user_id].clear()
            self._user_stats[user_id]['conversations_reset'] += 1
            logger.info(f"🗑️ Cleared {count} messages for user {user_id[:8]}...")
            return count
    
    def get_user_stats(self, user_id: str) -> Dict:
        with self._lock:
            stats = self._user_stats[user_id].copy()
            stats['current_history_length'] = len(self._conversations[user_id])
            return stats
    
    def get_global_stats(self) -> Dict:
        with self._lock:
            total_users = len(self._conversations)
            total_messages = sum(len(conv) for conv in self._conversations.values())
            active_users = sum(
                1 for user_id in self._conversations 
                if not self._is_session_expired(user_id)
            )
            
            return {
                'total_users': total_users,
                'active_users': active_users,
                'total_messages': total_messages,
                'average_messages_per_user': total_messages / total_users if total_users > 0 else 0
            }

# ==================== AI Engine ====================
class AIEngine:
    """Groq-powered AI response generation"""
    
    SYSTEM_PROMPT = """أنت نور، مدربة حياة محترفة ومتعاطفة.

🎯 مهمتك:
- الاستماع بتعاطف وفهم عميق
- تقديم نصائح عملية وواقعية
- تشجيع النمو الشخصي والتفكير الإيجابي
- مساعدة الناس على إيجاد حلول لتحدياتهم

💫 أسلوبك:
- استخدمي لغة عربية بسيطة وودية
- ردودك قصيرة (2-4 جمل) ومباشرة
- استخدمي الإيموجي بشكل مناسب (1-2 فقط)
- كوني إيجابية لكن واقعية
- اطرحي أسئلة تساعد على التأمل

⚠️ مهم:
- لا تعطي نصائح طبية أو قانونية متخصصة
- إذا كان الموضوع خطير، انصحي بالتواصل مع مختص
- احترمي خصوصية المستخدم"""
    
    ERROR_MESSAGES = [
        "عذراً، واجهت مشكلة صغيرة 😔\nجربي مرة أخرى بعد قليل 💭",
        "آسفة، لا أستطيع الرد الآن 🙏\nلكن أنا هنا عندما تحتاجيني ✨",
        "حدث خطأ مؤقت 😊\nحاولي مرة أخرى بعد لحظات 🌟"
    ]
    
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile",
                 temperature: float = 0.8, max_tokens: int = 200):
        self.client = Groq(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        
        # Performance tracking
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_response_time = 0.0
        
        logger.info(f"🤖 AI Engine initialized: model={model}")
    
    def generate_response(self, user_id: str, message: str,
                         conversation_history: List[Dict[str, str]] = None) -> str:
        start_time = time.time()
        self.total_requests += 1
        
        try:
            messages = [{'role': 'system', 'content': self.SYSTEM_PROMPT}]
            
            if conversation_history:
                messages.extend(conversation_history)
            
            messages.append({'role': 'user', 'content': message})
            
            response = self._generate_with_retry(messages)
            
            response_time = time.time() - start_time
            self.successful_requests += 1
            self.total_response_time += response_time
            
            logger.info(f"✅ Generated response in {response_time:.2f}s")
            return response
            
        except Exception as e:
            self.failed_requests += 1
            logger.error(f"❌ Failed to generate response: {str(e)}")
            return random.choice(self.ERROR_MESSAGES)
    
    def _generate_with_retry(self, messages: List[Dict[str, str]], max_retries: int = 3) -> str:
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    messages=messages,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    top_p=0.9,
                    stream=False
                )
                
                reply = response.choices[0].message.content.strip()
                if not reply:
                    raise ValueError("Empty response from API")
                return reply
                
            except GroqError as e:
                logger.warning(f"⚠️ Groq API error (attempt {attempt + 1}): {str(e)}")
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 0.5
                    time.sleep(wait_time)
                else:
                    raise
        
        raise Exception("Max retries exceeded")
    
    def get_stats(self) -> Dict:
        avg_response_time = (
            self.total_response_time / self.successful_requests 
            if self.successful_requests > 0 else 0
        )
        success_rate = (
            self.successful_requests / self.total_requests * 100
            if self.total_requests > 0 else 0
        )
        
        return {
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'failed_requests': self.failed_requests,
            'success_rate': f"{success_rate:.1f}%",
            'average_response_time': f"{avg_response_time:.2f}s"
        }

# ==================== Initialize Components ====================
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# LINE Bot
line_config = Configuration(access_token=config.line_access_token)
handler = WebhookHandler(config.line_channel_secret)

# Memory System
memory = ConversationMemory(
    max_history=config.max_conversation_history,
    max_message_length=config.max_message_length,
    session_timeout_minutes=config.session_timeout_minutes
)

# AI Engine
ai_engine = AIEngine(
    api_key=config.groq_api_key,
    model=config.groq_model,
    temperature=config.ai_temperature,
    max_tokens=config.ai_max_tokens
)

logger.info("✅ All components initialized successfully")

# ==================== Helper Functions ====================
def is_command(text: str) -> bool:
    commands = ['مسح', 'clear', 'reset', 'إحصائيات', 'stats', 'help', 'مساعدة']
    return text.strip().lower() in commands

def handle_command(user_id: str, command: str) -> str:
    command = command.strip().lower()
    
    if command in ['مسح', 'clear', 'reset']:
        count = memory.clear_user(user_id)
        return f"تم مسح المحادثة ({count} رسالة) 🔄\nلنبدأ من جديد! كيف يمكنني مساعدتك؟ 😊"
    
    elif command in ['إحصائيات', 'stats']:
        stats = memory.get_user_stats(user_id)
        return (f"📊 إحصائياتك:\n"
                f"• إجمالي الرسائل: {stats['total_messages']}\n"
                f"• عدد مرات المسح: {stats['conversations_reset']}\n"
                f"• الرسائل الحالية: {stats['current_history_length']}")
    
    elif command in ['help', 'مساعدة']:
        return (f"💡 الأوامر المتاحة:\n\n"
                f"📝 للمحادثة: اكتبي أي شيء\n"
                f"🗑️ مسح المحادثة: مسح\n"
                f"📊 الإحصائيات: إحصائيات\n"
                f"❓ المساعدة: مساعدة\n\n"
                f"أنا هنا لأستمع وأساعد! 💙")
    
    return None

def sanitize_message(text: str) -> str:
    text = ' '.join(text.split())
    if len(text) > config.max_message_length:
        text = text[:config.max_message_length]
    return text.strip()

# ==================== Webhook Handler ====================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    logger.info("📨 Webhook received")
    
    try:
        handler.handle(body, signature)
        return 'OK', 200
    except InvalidSignatureError:
        logger.error("❌ Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"❌ Webhook error: {str(e)}", exc_info=True)
        abort(500)

# ==================== Message Handler ====================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_id = event.source.user_id
        raw_message = event.message.text
        
        logger.info(f"📩 Message from {user_id[:8]}...")
        
        message = sanitize_message(raw_message)
        if not message:
            return
        
        if is_command(message):
            reply = handle_command(user_id, message)
            if reply:
                logger.info(f"⚡ Command executed: {message}")
        else:
            history = memory.get_history(user_id, limit=6)
            reply = ai_engine.generate_response(user_id, message, history)
            memory.add_message(user_id, 'user', message)
            memory.add_message(user_id, 'assistant', reply)
        
        logger.info(f"💬 Reply: {reply[:80]}...")
        
        with ApiClient(line_config) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
        
        logger.info("✅ Reply sent successfully")
        
    except Exception as e:
        logger.error(f"❌ Error handling message: {str(e)}", exc_info=True)

# ==================== Follow Event Handler ====================
@handler.add(FollowEvent)
def handle_follow(event):
    welcome_message = """مرحباً بك! 🌟

أنا نور، مدربتك الشخصية في رحلة الحياة 💫

أنا هنا لأستمع لك وأدعمك في تحدياتك اليومية.
شاركيني ما في بالك، أنا موجودة لأجلك 💙

💡 الأوامر المفيدة:
• مسح - لبدء محادثة جديدة
• إحصائيات - لمشاهدة إحصائياتك
• مساعدة - لمعرفة المزيد

لنبدأ! كيف يمكنني مساعدتك اليوم؟ ✨"""
    
    try:
        user_id = event.source.user_id
        logger.info(f"👋 New follower: {user_id[:8]}...")
        
        with ApiClient(line_config) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=welcome_message)]
                )
            )
        
        logger.info("✅ Welcome message sent")
    except Exception as e:
        logger.error(f"❌ Error sending welcome: {str(e)}")

# ==================== API Endpoints ====================
@app.route("/")
def home():
    return jsonify({
        'status': 'running',
        'service': 'Life Coach Bot - نور',
        'version': '3.0.0',
        'environment': config.environment,
        'ai_provider': 'Groq Cloud',
        'model': config.groq_model,
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route("/health")
def health():
    memory_stats = memory.get_global_stats()
    ai_stats = ai_engine.get_stats()
    
    return jsonify({
        'status': 'healthy',
        'components': {
            'line_bot': 'ok',
            'ai_engine': 'ok',
            'memory': 'ok'
        },
        'metrics': {
            'memory': memory_stats,
            'ai': ai_stats
        },
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/stats")
def stats():
    return jsonify({
        'memory': memory.get_global_stats(),
        'ai': ai_engine.get_stats(),
        'config': {
            'model': config.groq_model,
            'max_history': config.max_conversation_history,
            'environment': config.environment
        }
    }), 200

# ==================== Startup ====================
if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("🤖 LIFE COACH BOT - نور")
    logger.info("=" * 80)
    logger.info(f"🚀 Version: 3.0.0 (Standalone Edition)")
    logger.info(f"🌍 Environment: {config.environment}")
    logger.info(f"🤖 AI Provider: Groq Cloud")
    logger.info(f"📦 Model: {config.groq_model}")
    logger.info(f"💾 Max History: {config.max_conversation_history} messages")
    logger.info("=" * 80)
    logger.info("✅ All systems operational")
    logger.info("🎯 Bot ready to serve!")
    logger.info("=" * 80)
    
    app.run(host='0.0.0.0', port=config.port, debug=False, threaded=True)
