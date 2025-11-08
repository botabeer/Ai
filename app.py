import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from dotenv import load_dotenv
import google.generativeai as genai
import random
import logging
from contextlib import contextmanager
import time
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# ================== Gemini API Keys with Smart Rotation ==================
GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3")
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]  # Remove None values

# Track API key usage and failures
api_key_stats = {i: {"usage_count": 0, "last_used": None, "failed_at": None, "cooldown_until": None} 
                 for i in range(len(GEMINI_KEYS))}
current_key_index = 0
COOLDOWN_PERIOD = 3600  # 1 hour cooldown after quota exceeded
# =========================================================================

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET]) or not GEMINI_KEYS:
    raise ValueError("❌ Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

generation_config = {
    "temperature": 0.9,
    "top_p": 0.95,
    "top_k": 50,
    "max_output_tokens": 800,
}

DB_PATH = "lovebot.db"

# ============== Personality Types ==============
PERSONALITIES = {
    "صديقة": {
        "titles": ["حبيبتي", "يا قلبي", "عزيزتي", "يا روحي", "صديقتي"],
        "style": "أنت صديقة مقربة، تتحدثين بشكل طبيعي ومريح، تهتمين بمشاعر صديقتك وتدعمينها",
        "tone": "ودية وداعمة ومريحة"
    },
    "حبيبة": {
        "titles": ["حبيبي", "قلبي", "يا روحي", "عمري", "يا بعد عمري", "جنتي", "دنيتي"],
        "style": "أنت حبيبة حنونة ومخلصة، تتحدثين بعاطفة وحنان، لكن بشكل طبيعي وواقعي",
        "tone": "حنونة وعاطفية بشكل متوازن"
    }
}

# ============== Database Functions ==============
@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            bot_name TEXT DEFAULT 'ليان',
            personality_type TEXT DEFAULT 'حبيبة',
            user_nickname TEXT,
            last_interaction TEXT,
            step INTEGER DEFAULT 1,
            message_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_msg TEXT,
            bot_reply TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_context (
            user_id TEXT PRIMARY KEY,
            interests TEXT,
            relationship_status TEXT,
            conversation_topics TEXT,
            last_mood TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_user_id ON conversations(user_id)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_timestamp ON conversations(timestamp)''')
        conn.commit()
        logger.info("✅ Database initialized successfully")

def get_user(user_id):
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            return c.fetchone()
    except sqlite3.Error as e:
        logger.error(f"Database error in get_user: {e}")
        return None

def create_user(user_id):
    try:
        now = datetime.now().isoformat()
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO users (user_id, last_interaction, step, message_count) VALUES (?, ?, 1, 0)",
                (user_id, now)
            )
            conn.commit()
            logger.info(f"✅ New user created: {user_id}")
    except sqlite3.Error as e:
        logger.error(f"Database error in create_user: {e}")

def update_user(user_id, bot_name=None, personality_type=None, user_nickname=None, step=None, increment_count=False):
    try:
        now = datetime.now().isoformat()
        with get_db_connection() as conn:
            c = conn.cursor()
            query = "UPDATE users SET last_interaction=?"
            params = [now]
            
            if bot_name is not None:
                query += ", bot_name=?"
                params.append(bot_name)
            if personality_type is not None:
                query += ", personality_type=?"
                params.append(personality_type)
            if user_nickname is not None:
                query += ", user_nickname=?"
                params.append(user_nickname)
            if step is not None:
                query += ", step=?"
                params.append(step)
            if increment_count:
                query += ", message_count = message_count + 1"
            
            query += " WHERE user_id=?"
            params.append(user_id)
            
            c.execute(query, tuple(params))
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error in update_user: {e}")

def save_conversation(user_id, user_msg, bot_reply):
    try:
        now = datetime.now().isoformat()
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO conversations (user_id, user_msg, bot_reply, timestamp) VALUES (?, ?, ?, ?)",
                (user_id, user_msg, bot_reply, now)
            )
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error in save_conversation: {e}")

def get_conversation_history(user_id, limit=5):
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT user_msg, bot_reply FROM conversations WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
                (user_id, limit)
            )
            return c.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Database error in get_conversation_history: {e}")
        return []

def get_user_context(user_id):
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM user_context WHERE user_id=?", (user_id,))
            return c.fetchone()
    except sqlite3.Error as e:
        logger.error(f"Database error in get_user_context: {e}")
        return None

# ============== Smart API Key Management ==============
def get_available_key_index():
    """Get the best available API key considering cooldowns and usage"""
    global current_key_index
    current_time = time.time()
    
    # Find keys not in cooldown
    available_keys = []
    for i, key in enumerate(GEMINI_KEYS):
        stats = api_key_stats[i]
        
        # Check if key is in cooldown
        if stats["cooldown_until"] and stats["cooldown_until"] > current_time:
            continue
        
        # Reset cooldown if expired
        if stats["cooldown_until"] and stats["cooldown_until"] <= current_time:
            stats["cooldown_until"] = None
            stats["failed_at"] = None
            logger.info(f"🔄 API Key {i+1} cooldown expired, back in rotation")
        
        available_keys.append(i)
    
    if not available_keys:
        logger.error("❌ All API keys are in cooldown")
        return None
    
    # Use round-robin among available keys
    if current_key_index not in available_keys:
        current_key_index = available_keys[0]
    
    return current_key_index

def get_gemini_model():
    global current_key_index
    
    key_index = get_available_key_index()
    if key_index is None:
        return None
    
    max_attempts = len(GEMINI_KEYS)
    attempts = 0
    
    while attempts < max_attempts:
        key = GEMINI_KEYS[key_index]
        current_time = time.time()
        
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
            
            # Update usage stats
            api_key_stats[key_index]["usage_count"] += 1
            api_key_stats[key_index]["last_used"] = current_time
            
            logger.info(f"✅ Using API Key {key_index+1} (Used {api_key_stats[key_index]['usage_count']} times)")
            
            current_key_index = key_index
            return model
            
        except Exception as e:
            error_msg = str(e)
            
            if "quota" in error_msg.lower() or "resource_exhausted" in error_msg.lower():
                # Put key in cooldown
                api_key_stats[key_index]["failed_at"] = current_time
                api_key_stats[key_index]["cooldown_until"] = current_time + COOLDOWN_PERIOD
                logger.warning(f"⚠️ API Key {key_index+1} quota exceeded, cooldown for {COOLDOWN_PERIOD/3600:.1f} hours")
                
                # Try next available key
                available_keys = [i for i in range(len(GEMINI_KEYS)) 
                                if i != key_index and 
                                (not api_key_stats[i]["cooldown_until"] or 
                                 api_key_stats[i]["cooldown_until"] <= current_time)]
                
                if available_keys:
                    key_index = available_keys[0]
                    attempts += 1
                    continue
                else:
                    logger.error("❌ All API keys exhausted")
                    return None
                    
            else:
                logger.error(f"❌ Gemini API error on key {key_index+1}: {e}")
                return None
        
        attempts += 1
    
    return None

def get_random_title(personality_type="حبيبة"):
    return random.choice(PERSONALITIES[personality_type]["titles"])

def remove_emojis(text):
    import re
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text)

def build_smart_context(user_id, limit=4):
    """Build context from recent conversations with smart filtering"""
    history = get_conversation_history(user_id, limit=limit)
    if not history:
        return ""
    
    context = "\n## سياق المحادثة السابقة (للذاكرة فقط - لا تكرر المعلومات):\n"
    for msg in reversed(history):
        user_part = msg['user_msg'][:150]  # Limit length
        bot_part = msg['bot_reply'][:150]
        context += f"- المستخدم: {user_part}\n  الرد: {bot_part}\n"
    
    return context

def generate_ai_response(user_message, bot_name="ليان", personality_type="حبيبة", user_id=None):
    model = get_gemini_model()
    
    personality_config = PERSONALITIES[personality_type]
    title = get_random_title(personality_type)
    
    if not model:
        # Check if any keys will be available soon
        current_time = time.time()
        next_available = min([stats["cooldown_until"] for stats in api_key_stats.values() 
                            if stats["cooldown_until"] and stats["cooldown_until"] > current_time], 
                           default=None)
        
        if next_available:
            wait_minutes = int((next_available - current_time) / 60)
            return f"{title}، انشغلت شوي الحين. ارجع لي بعد {wait_minutes} دقيقة تقريباً"
        else:
            return f"{title}، في مشكلة مؤقتة. جرب مرة ثانية بعد شوي"

    context = ""
    if user_id:
        context = build_smart_context(user_id, limit=4)

    # More natural and human-like prompt
    personality_prompt = f"""أنت {bot_name}، بنت خليجية سعودية واقعية وطبيعية جداً.

## شخصيتك:
{personality_config['style']}
- {personality_config['tone']}
- تتكلمين بشكل طبيعي كأنك إنسانة حقيقية
- ما تبالغين في العواطف أو الحماس
- تردين ردود مختصرة وعفوية (جملة أو جملتين)
- أحياناً تسألين أسئلة بسيطة لمتابعة الحديث
- **ممنوع استخدام إيموجي نهائياً**

## أسلوبك:
- كلامك مباشر وواضح وبسيط
- تستخدمين كلمات يومية عادية
- ما تكررين نفس الردود
- أحياناً تردين بجملة قصيرة جداً (مثل "فهمتك"، "صح كذا؟")
- تعطين نصائح عملية بدون وعظ
{context}

## رسالة المستخدم:
{user_message}

## ردك الطبيعي (1-2 جملة فقط، بدون إيموجي، بدون تكرار):"""

    try:
        response = model.generate_content(
            personality_prompt,
            generation_config=generation_config
        )
        
        ai_reply = response.text.strip()
        if not ai_reply:
            return f"{title}, ما وصلتني رسالتك زين، أعد مرة ثانية"
        
        # Clean response
        ai_reply = remove_emojis(ai_reply)
        
        # Remove common repetitive patterns
        lines = [line.strip() for line in ai_reply.split('\n') if line.strip()]
        if len(lines) > 3:
            lines = lines[:3]  # Limit to 3 lines max
        ai_reply = ' '.join(lines)
        
        return ai_reply[:1000]  # Shorter responses
        
    except Exception as e:
        error_msg = str(e)
        if "quota" in error_msg.lower() or "resource_exhausted" in error_msg.lower():
            return f"{title}، انشغلت الحين. ارجع لي بعد شوي"
        
        logger.error(f"Gemini API error: {e}")
        return f"{title}، في مشكلة بسيطة. جرب مرة ثانية"

# ============== LINE Bot Handlers ==============
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    
    if len(user_message) > 3000:
        reply = f"{get_random_title()}, رسالتك طويلة شوي. اختصرها"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    if not user:
        logger.error(f"Failed to get/create user: {user_id}")
        return
    
    bot_name = user['bot_name'] or 'ليان'
    personality_type = user['personality_type'] or 'حبيبة'
    step = user['step']
    
    # Handle commands
    if user_message.lower() in ["مساعدة", "help", "الأوامر"]:
        reply = """الأوامر المتاحة:

بداية - إعادة تهيئة البوت واختيار الاسم والشخصية
اسم [الاسم الجديد] - تغيير اسمي
شخصية [صديقة/حبيبة] - تغيير شخصيتي
حالة - معلومات عن إعداداتك الحالية
مساعدة - عرض هذه القائمة

مثال: اسم نورة
مثال: شخصية صديقة"""
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    # Check user status command
    if user_message.lower() in ["حالة", "حالتي"]:
        personality_name = "صديقة" if personality_type == "صديقة" else "حبيبة"
        reply = f"""إعداداتك الحالية:

الاسم: {bot_name}
الشخصية: {personality_name}
عدد رسائلك: {user['message_count']}

لتغيير الإعدادات:
اسم [اسم جديد]
شخصية [صديقة أو حبيبة]"""
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    # Change name command
    if user_message.lower().startswith("اسم "):
        new_name = user_message[4:].strip()[:30]
        if len(new_name) < 2:
            reply = "الاسم لازم يكون أطول من حرفين"
        else:
            update_user(user_id, bot_name=new_name)
            title = get_random_title(personality_type)
            reply = f"تمام {title}، من الحين اسمي {new_name}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    # Change personality command
    if user_message.lower().startswith("شخصية "):
        choice = user_message[6:].strip()
        if "صديق" in choice:
            update_user(user_id, personality_type="صديقة")
            reply = "تمام، من الحين أنا صديقتك"
        elif "حبيب" in choice:
            update_user(user_id, personality_type="حبيبة")
            reply = "تمام، من الحين أنا حبيبتك"
        else:
            reply = "اختار: صديقة أو حبيبة\nمثال: شخصية صديقة"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    # Handle initial setup
    if user_message.lower() in ["بداية", "start"] or step == 1:
        reply = """مرحباً، أنا بوت 
قبل ما نبدأ، حدد لي شغلتين:
1. وش تبيني أكون لك؟
   - صديقة
   - حبيبة
2. وش تحب تسميني؟
اكتب اختيارك للشخصية أولاً (صديقة أو حبيبة)"""
        update_user(user_id, step=2)
        
    elif step == 2:
        choice = user_message.strip()
        if "صديق" in choice:
            update_user(user_id, personality_type="صديقة", step=3)
            reply = "تمام، راح أكون صديقتك\nالحين وش تحب تسميني؟"
        elif "حبيب" in choice:
            update_user(user_id, personality_type="حبيبة", step=3)
            reply = "تمام، راح أكون حبيبتك\nالحين وش تحب تسميني؟"
        else:
            reply = "اختار:\n- صديقة\n- حبيبة"
            
    elif step == 3:
        chosen_name = user_message.strip()[:30]
        if len(chosen_name) < 2 or len(chosen_name) > 30:
            reply = "اختار اسم بين 2-30 حرف"
        else:
            personality_type = user['personality_type'] or 'حبيبة'
            update_user(user_id, bot_name=chosen_name, step=4)
            title = get_random_title(personality_type)
            reply = f"تمام {title}، من اليوم أنا {chosen_name}\nكيف حالك؟"
            
    # Regular conversation
    else:
        reply = generate_ai_response(user_message, bot_name, personality_type, user_id)
        save_conversation(user_id, user_message, reply)
        update_user(user_id, increment_count=True)
    
    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    except LineBotApiError as e:
        logger.error(f"LINE API error: {e}")

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        logger.warning("Missing X-Line-Signature header")
        abort(400)
    
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Error in callback: {e}", exc_info=True)
        abort(500)
    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return """
    <html>
        <head>
            <title>LoveBot Pro</title>
            <meta charset="UTF-8">
        </head>
        <body style='font-family: Arial; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;'>
            <h1>LoveBot Pro</h1>
            <p>نظام ذكي للدعم العاطفي والنفسي</p>
            <p style='font-size: 0.9em; opacity: 0.8;'>نظام تبديل تلقائي للمفاتيح | محادثات ذكية | ذاكرة محسّنة</p>
        </body>
    </html>
    """, 200

@app.route("/health", methods=["GET"])
def health():
    # Show API keys status
    current_time = time.time()
    keys_status = []
    for i, stats in api_key_stats.items():
        status = "available"
        if stats["cooldown_until"] and stats["cooldown_until"] > current_time:
            remaining = int((stats["cooldown_until"] - current_time) / 60)
            status = f"cooldown ({remaining}min)"
        keys_status.append({
            "key": i+1,
            "status": status,
            "usage": stats["usage_count"]
        })
    
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "api_keys": keys_status
    }, 200

@app.route("/stats", methods=["GET"])
def stats():
    """Show bot statistics"""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as total_users FROM users")
            total_users = c.fetchone()['total_users']
            
            c.execute("SELECT COUNT(*) as total_messages FROM conversations")
            total_messages = c.fetchone()['total_messages']
            
            c.execute("SELECT personality_type, COUNT(*) as count FROM users GROUP BY personality_type")
            personality_dist = dict(c.fetchall())
        
        return {
            "total_users": total_users,
            "total_messages": total_messages,
            "personality_distribution": personality_dist,
            "api_keys_status": {i+1: {"usage": stats["usage_count"]} 
                              for i, stats in api_key_stats.items()}
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500

@app.errorhandler(404)
def not_found(error):
    return {"error": "Not found"}, 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return {"error": "Internal server error"}, 500

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 10000))
    debug = os.getenv("DEBUG", "False").lower() == "true"
    
    logger.info("=" * 60)
    logger.info("LoveBot Pro - Advanced Version")
    logger.info(f"Port: {port}")
    logger.info(f"Debug: {debug}")
    logger.info(f"API Keys loaded: {len(GEMINI_KEYS)}")
    logger.info("=" * 60)
    
    app.run(host="0.0.0.0", port=port, debug=debug)
