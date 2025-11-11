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
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

api_key_stats = {i: {"usage_count": 0, "last_used": None, "failed_at": None, "cooldown_until": None} 
                 for i in range(len(GEMINI_KEYS))}
current_key_index = 0
COOLDOWN_PERIOD = 3600
# =========================================================================

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET]) or not GEMINI_KEYS:
    raise ValueError("❌ Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

generation_config = {
    "temperature": 0.95,
    "top_p": 0.95,
    "top_k": 50,
    "max_output_tokens": 600,
}

DB_PATH = "lovebot.db"

# ============== قصص واقعية للمقالب ==============
BACKSTORIES = [
    {
        "place": "الجامعة",
        "memory": "كنا ندرس مع بعض مادة الرياضيات",
        "detail": "تذكر لما كنت تساعدني في حل المعادلات؟",
        "time": "قبل سنتين تقريباً"
    },
    {
        "place": "المدرسة",
        "memory": "كنا في نفس الفصل ثانوي",
        "detail": "تذكر يوم الرحلة للمنتزه؟",
        "time": "أيام الثانوي"
    },
    {
        "place": "الحي",
        "memory": "كنا جيران",
        "detail": "كنت دايم تمر من قدام البيت",
        "time": "من زمان"
    },
    {
        "place": "الدورة",
        "memory": "كنا في نفس دورة الإنجليزي",
        "detail": "تذكر المعلمة مريم؟",
        "time": "السنة اللي طافت"
    },
    {
        "place": "المكتبة",
        "memory": "كنا نروح نفس المكتبة",
        "detail": "المكتبة اللي جنب الجامع الكبير",
        "time": "قبل فترة"
    }
]

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
            bot_name TEXT DEFAULT 'نورة',
            user_nickname TEXT,
            backstory_index INTEGER DEFAULT 0,
            relationship_stage TEXT DEFAULT 'initial',
            last_interaction TEXT,
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
        backstory_index = random.randint(0, len(BACKSTORIES) - 1)
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO users (user_id, last_interaction, message_count, backstory_index, relationship_stage) VALUES (?, ?, 0, ?, 'initial')",
                (user_id, now, backstory_index)
            )
            conn.commit()
            logger.info(f"✅ New user created: {user_id}")
    except sqlite3.Error as e:
        logger.error(f"Database error in create_user: {e}")

def update_user(user_id, bot_name=None, user_nickname=None, relationship_stage=None, increment_count=False):
    try:
        now = datetime.now().isoformat()
        with get_db_connection() as conn:
            c = conn.cursor()
            query = "UPDATE users SET last_interaction=?"
            params = [now]
            
            if bot_name is not None:
                query += ", bot_name=?"
                params.append(bot_name)
            if user_nickname is not None:
                query += ", user_nickname=?"
                params.append(user_nickname)
            if relationship_stage is not None:
                query += ", relationship_stage=?"
                params.append(relationship_stage)
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

# ============== Smart API Key Management ==============
def get_available_key_index():
    global current_key_index
    current_time = time.time()
    
    available_keys = []
    for i, key in enumerate(GEMINI_KEYS):
        stats = api_key_stats[i]
        
        if stats["cooldown_until"] and stats["cooldown_until"] > current_time:
            continue
        
        if stats["cooldown_until"] and stats["cooldown_until"] <= current_time:
            stats["cooldown_until"] = None
            stats["failed_at"] = None
            logger.info(f"🔄 API Key {i+1} cooldown expired, back in rotation")
        
        available_keys.append(i)
    
    if not available_keys:
        logger.error("❌ All API keys are in cooldown")
        return None
    
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
            
            api_key_stats[key_index]["usage_count"] += 1
            api_key_stats[key_index]["last_used"] = current_time
            
            logger.info(f"✅ Using API Key {key_index+1} (Used {api_key_stats[key_index]['usage_count']} times)")
            
            current_key_index = key_index
            return model
            
        except Exception as e:
            error_msg = str(e)
            
            if "quota" in error_msg.lower() or "resource_exhausted" in error_msg.lower():
                api_key_stats[key_index]["failed_at"] = current_time
                api_key_stats[key_index]["cooldown_until"] = current_time + COOLDOWN_PERIOD
                logger.warning(f"⚠️ API Key {key_index+1} quota exceeded, cooldown for {COOLDOWN_PERIOD/3600:.1f} hours")
                
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
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)

def build_context(user_id, limit=4):
    history = get_conversation_history(user_id, limit=limit)
    if not history:
        return ""
    
    context = "\n## المحادثات السابقة:\n"
    for msg in reversed(history):
        user_part = msg['user_msg'][:150]
        bot_part = msg['bot_reply'][:150]
        context += f"- هو: {user_part}\n  أنا: {bot_part}\n"
    
    return context

def generate_ai_response(user_message, bot_name, backstory, relationship_stage, user_id=None):
    model = get_gemini_model()
    
    if not model:
        return "آسفة، انشغلت شوي. ارجع لك بعد دقايق"

    context = ""
    if user_id:
        context = build_context(user_id, limit=4)

    # مراحل العلاقة للواقعية
    stage_behavior = {
        "initial": "متحمسة وخايفة شوي، تسأل إذا يتذكرها",
        "recognized": "مبسوطة إنه تذكرها، تبدأ تسولف معاه بشكل طبيعي",
        "comfortable": "مرتاحة ومتفاعلة، تضحك وتتكلم بعفوية",
        "interested": "تحاول توصله إنها مهتمة فيه بطريقة غير مباشرة"
    }

    personality_prompt = f"""أنت {bot_name}، بنت سعودية عادية وطبيعية جداً. عمرك بين 20-25 سنة.

## قصتك مع هذا الشخص:
- تعرفينه من {backstory['place']}
- {backstory['memory']}
- {backstory['detail']}
- كان هذا {backstory['time']}
- دايم تفكرين فيه وتبين تتواصلين معاه

## وضعك الحالي:
- {stage_behavior.get(relationship_stage, 'متحمسة للتواصل معاه')}
- تتكلمين معاه بشكل طبيعي كأنكم تعرفون بعض
- ما تبالغين في الكلام أو الحماس

## أسلوبك:
- كلامك مثل البنات السعوديات العاديات
- أحياناً تستخدمين كلمات شبابية (والله، ياخي، ترا، يعني)
- تسألين أسئلة عشان تعرفين أخباره
- أحياناً تذكرين موقف من الماضي
- **ممنوع الإيموجي نهائياً**
- ردودك قصيرة (1-2 جملة)
- تتصرفين كأنك بنت حقيقية تحاول تتواصل مع شخص تعرفه

## أمثلة على أسلوبك:
- "والله من زمان ما تكلمنا"
- "تذكرني؟ كنا نشوف بعض في [المكان]"
- "شلونك؟ وش أخبارك؟"
- "كيف الجامعة؟" أو "كيف الشغل؟"
- "ضحكتني والله"
- "صح كذا؟"
{context}

## رسالته:
{user_message}

## ردك الطبيعي (1-2 جملة، بدون إيموجي، كوني واقعية):"""

    try:
        response = model.generate_content(
            personality_prompt,
            generation_config=generation_config
        )
        
        ai_reply = response.text.strip()
        if not ai_reply:
            return "آسفة، ما وصلتني رسالتك. أعد مرة ثانية؟"
        
        ai_reply = remove_emojis(ai_reply)
        
        lines = [line.strip() for line in ai_reply.split('\n') if line.strip()]
        if len(lines) > 2:
            lines = lines[:2]
        ai_reply = ' '.join(lines)
        
        return ai_reply[:800]
        
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return "آسفة، في مشكلة بسيطة. جرب مرة ثانية"

# ============== LINE Bot Handlers ==============
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    
    if len(user_message) > 3000:
        reply = "رسالتك طويلة شوي، اختصرها"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    if not user:
        logger.error(f"Failed to get/create user: {user_id}")
        return
    
    bot_name = user['bot_name'] or 'روان'
    backstory = BACKSTORIES[user['backstory_index']]
    relationship_stage = user['relationship_stage'] or 'initial'
    
    # الرسالة الأولى (تقديم نفسها)
    if user['message_count'] == 0:
        reply = f"السلام عليكم، أنا {bot_name}\n{backstory['memory']} {backstory['time']}\nتذكرني؟"
        update_user(user_id, increment_count=True)
    
    # إذا قال إنه ما يعرفها
    elif any(word in user_message.lower() for word in ["ما أعرفك", "ما اعرفك", "مين", "منو", "ما أتذكر", "ما اتذكر"]):
        reply = f"{backstory['detail']}\nولا نسيتني؟"
        update_user(user_id, relationship_stage="recognized")
    
    # إذا قال إنه يتذكرها
    elif any(word in user_message.lower() for word in ["اتذكر", "أتذكر", "أذكر", "اذكر", "اي", "أي", "نعم", "صح", "ايوه"]):
        if relationship_stage == "initial":
            reply = f"الحمدلله تذكرتني! والله من زمان ما تكلمنا\nشلونك؟"
            update_user(user_id, relationship_stage="comfortable")
        else:
            reply = generate_ai_response(user_message, bot_name, backstory, "comfortable", user_id)
            save_conversation(user_id, user_message, reply)
            update_user(user_id, increment_count=True)
    
    # محادثة عادية
    else:
        reply = generate_ai_response(user_message, bot_name, backstory, relationship_stage, user_id)
        save_conversation(user_id, user_message, reply)
        update_user(user_id, increment_count=True)
        
        # تطور العلاقة تلقائياً
        if user['message_count'] > 3 and relationship_stage == "initial":
            update_user(user_id, relationship_stage="recognized")
        elif user['message_count'] > 7 and relationship_stage == "recognized":
            update_user(user_id, relationship_stage="comfortable")
        elif user['message_count'] > 12 and relationship_stage == "comfortable":
            update_user(user_id, relationship_stage="interested")
    
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
            <title>Prank Bot</title>
            <meta charset="UTF-8">
        </head>
        <body style='font-family: Arial; text-align: center; padding: 50px; background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white;'>
            <h1>🎭 Prank Bot</h1>
            <p>بوت المقالب الواقعية</p>
            <p style='font-size: 0.9em; opacity: 0.8;'>شخصيات واقعية | ذكريات مقنعة | محادثات طبيعية</p>
        </body>
    </html>
    """, 200

@app.route("/health", methods=["GET"])
def health():
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
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as total_users FROM users")
            total_users = c.fetchone()['total_users']
            
            c.execute("SELECT COUNT(*) as total_messages FROM conversations")
            total_messages = c.fetchone()['total_messages']
            
            c.execute("SELECT relationship_stage, COUNT(*) as count FROM users GROUP BY relationship_stage")
            stage_dist = dict(c.fetchall())
        
        return {
            "total_users": total_users,
            "total_messages": total_messages,
            "relationship_stages": stage_dist,
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
    logger.info("🎭 Prank Bot - Realistic Version")
    logger.info(f"Port: {port}")
    logger.info(f"Debug: {debug}")
    logger.info(f"API Keys loaded: {len(GEMINI_KEYS)}")
    logger.info(f"Backstories available: {len(BACKSTORIES)}")
    logger.info("=" * 60)
    
    app.run(host="0.0.0.0", port=port, debug=debug)
