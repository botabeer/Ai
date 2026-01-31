"""
🤖 Life Coach LINE Bot - Professional Edition
==============================================
✨ Enterprise-grade chatbot with advanced features
"""

from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
import logging
from datetime import datetime
import os
from dotenv import load_dotenv

# Local imports
from config import config
from memory import ConversationMemory
from ai_engine import AIEngine

# Load environment variables
load_dotenv()

# ==================== Logging Setup ====================
def setup_logging(log_level: str = 'INFO'):
    """Configure logging with custom format"""
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Reduce noise from libraries
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

setup_logging(config.app.log_level)
logger = logging.getLogger(__name__)

# ==================== Application Setup ====================
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # Support Arabic JSON

# Validate configuration
if not config.validate_all():
    logger.critical("💥 Configuration validation failed. Exiting.")
    exit(1)

# Print configuration summary
config.print_summary()

# ==================== Initialize Components ====================
# LINE Bot
line_config = Configuration(access_token=config.line.access_token)
handler = WebhookHandler(config.line.channel_secret)

# Memory System
memory = ConversationMemory(
    max_history=config.app.max_conversation_history,
    max_message_length=config.app.max_message_length,
    session_timeout_minutes=config.app.session_timeout_minutes
)

# AI Engine
ai_engine = AIEngine(
    api_key=config.groq.api_key,
    model=config.groq.model,
    temperature=config.groq.temperature,
    max_tokens=config.groq.max_tokens
)

logger.info("✅ All components initialized successfully")

# ==================== Helper Functions ====================
def is_command(text: str) -> bool:
    """Check if message is a command"""
    commands = ['مسح', 'clear', 'reset', 'إحصائيات', 'stats', 'help', 'مساعدة']
    return text.strip().lower() in commands

def handle_command(user_id: str, command: str) -> str:
    """Handle special commands"""
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
    """Clean and validate user message"""
    # Remove excessive whitespace
    text = ' '.join(text.split())
    
    # Truncate if too long
    if len(text) > config.app.max_message_length:
        text = text[:config.app.max_message_length]
    
    return text.strip()

# ==================== Webhook Handler ====================
@app.route("/callback", methods=['POST'])
def callback():
    """
    LINE webhook endpoint
    Handles incoming events from LINE platform
    """
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    logger.info("📨 Webhook received")
    
    try:
        handler.handle(body, signature)
        return 'OK', 200
        
    except InvalidSignatureError:
        logger.error("❌ Invalid signature - possible security issue")
        abort(400)
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {str(e)}", exc_info=True)
        abort(500)

# ==================== Message Handler ====================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """
    Handle text messages from users
    
    Flow:
    1. Validate and sanitize message
    2. Check for commands
    3. Generate AI response with context
    4. Send reply via LINE
    """
    try:
        user_id = event.source.user_id
        raw_message = event.message.text
        
        logger.info(f"📩 Message from {user_id[:8]}...")
        logger.debug(f"   Content: {raw_message[:100]}")
        
        # Sanitize message
        message = sanitize_message(raw_message)
        
        if not message:
            logger.warning("⚠️ Empty message after sanitization")
            return
        
        # Check for commands
        if is_command(message):
            reply = handle_command(user_id, message)
            if reply:
                logger.info(f"⚡ Command executed: {message}")
        else:
            # Check if first time user
            user_stats = memory.get_user_stats(user_id)
            is_first_time = user_stats['total_messages'] == 0
            
            # Get conversation history
            history = memory.get_history(user_id, limit=6)
            
            # Generate AI response
            reply = ai_engine.generate_response(
                user_id=user_id,
                message=message,
                conversation_history=history,
                is_first_time=is_first_time
            )
            
            # Save to memory
            memory.add_message(user_id, 'user', message)
            memory.add_message(user_id, 'assistant', reply)
        
        logger.info(f"💬 Reply: {reply[:80]}...")
        
        # Send reply via LINE
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
        
        # Try to send error message to user
        try:
            error_reply = "عذراً، حدث خطأ 😔\nجربي مرة أخرى بعد قليل 💭"
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=error_reply)]
                    )
                )
        except:
            pass

# ==================== Follow Event Handler ====================
@handler.add(FollowEvent)
def handle_follow(event):
    """
    Handle new friend/follower event
    Send welcome message to new users
    """
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

# ==================== Health Check Endpoints ====================
@app.route("/")
def home():
    """Main endpoint - API information"""
    return jsonify({
        'status': 'running',
        'service': 'Life Coach Bot - نور',
        'version': '3.0.0',
        'environment': config.app.environment,
        'ai_provider': 'Groq Cloud',
        'model': config.groq.model,
        'uptime': 'healthy',
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route("/health")
def health():
    """Health check endpoint for monitoring"""
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
    """Simple ping endpoint"""
    return "pong", 200

@app.route("/stats")
def stats():
    """Detailed statistics endpoint"""
    return jsonify({
        'memory': memory.get_global_stats(),
        'ai': ai_engine.get_stats(),
        'config': {
            'model': config.groq.model,
            'max_history': config.app.max_conversation_history,
            'environment': config.app.environment
        }
    }), 200

@app.route("/admin/cleanup")
def admin_cleanup():
    """Admin endpoint to cleanup expired sessions"""
    cleaned = memory.cleanup_expired_sessions()
    return jsonify({
        'status': 'success',
        'sessions_cleaned': cleaned,
        'timestamp': datetime.now().isoformat()
    }), 200

# ==================== Error Handlers ====================
@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Internal error: {str(error)}")
    return jsonify({'error': 'Internal server error'}), 500

# ==================== Startup ====================
def print_startup_banner():
    """Print beautiful startup banner"""
    logger.info("=" * 80)
    logger.info("🤖 LIFE COACH BOT - نور")
    logger.info("=" * 80)
    logger.info(f"🚀 Version: 3.0.0 (Professional Edition)")
    logger.info(f"🌍 Environment: {config.app.environment}")
    logger.info(f"🤖 AI Provider: Groq Cloud")
    logger.info(f"📦 Model: {config.groq.model}")
    logger.info(f"💾 Max History: {config.app.max_conversation_history} messages")
    logger.info(f"⏰ Session Timeout: {config.app.session_timeout_minutes} minutes")
    logger.info(f"🔒 Rate Limit: {config.app.rate_limit_per_minute} req/min")
    logger.info(f"📝 Log Level: {config.app.log_level}")
    logger.info("=" * 80)
    logger.info("✅ All systems operational")
    logger.info("🎯 Bot ready to serve!")
    logger.info("=" * 80)

if __name__ == "__main__":
    print_startup_banner()
    
    port = config.app.port
    debug = config.app.environment == 'development'
    
    logger.info(f"🌐 Starting server on port {port}...")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug,
        threaded=True
    )
