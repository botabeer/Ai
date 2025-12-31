import os
import sqlite3
import warnings
from datetime import datetime, timedelta
from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    ShowLoadingAnimationRequest
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent, UnfollowEvent
from dotenv import load_dotenv
import logging
import time
import threading
import re
from collections import defaultdict
from queue import Queue

# Suppress FutureWarning for google.generativeai
warnings.filterwarnings('ignore', category=FutureWarning, module='google.generativeai')
import google.generativeai as genai

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

load_dotenv()
app = Flask(__name__)

# Configuration
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
    os.getenv("GEMINI_API_KEY_4"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

GEMINI_MODELS = [
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "")
MAX_DAILY_MESSAGES = int(os.getenv("MAX_DAILY_MESSAGES", "100"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "2"))

BOT_NAME = "Smart Assistant"
BOT_VERSION = "2.7"
BOT_CREATOR = "عبير الدوسري"
BOT_YEAR = "2025"

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise ValueError("Missing LINE credentials")

if not GEMINI_KEYS:
    raise ValueError("Missing Gemini API keys")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Database
DB_PATH = "chatbot.db"
message_queue = Queue()

# Simple DB connection
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# Rate Limiter
class RateLimiter:
    def __init__(self):
        self.requests = defaultdict(list)
    
    def is_allowed(self, user_id, seconds=2):
        now = time.time()
        self.requests[user_id] = [t for t in self.requests[user_id] if now - t < 60]
        
        if self.requests[user_id] and now - self.requests[user_id][-1] < seconds:
            return False
        
        self.requests[user_id].append(now)
        return True

rate_limiter = RateLimiter()

# Model Manager
class ModelManager:
    def __init__(self, keys, models):
        self.keys = keys
        self.models = models
        self.current_key = 0
        self.current_model = 0
        self.fails = defaultdict(int)
        logger.info(f"Initialized: {len(keys)} keys, {len(models)} models")
    
    def get_next(self):
        combo = f"k{self.current_key}_m{self.current_model}"
        key = self.keys[self.current_key]
        model = self.models[self.current_model]
        return key, model, combo
    
    def mark_fail(self, combo):
        self.fails[combo] += 1
        self.current_model = (self.current_model + 1) % len(self.models)
        if self.current_model == 0:
            self.current_key = (self.current_key + 1) % len(self.keys)
    
    def mark_success(self, combo):
        self.fails[combo] = max(0, self.fails[combo] - 1)

model_manager = ModelManager(GEMINI_KEYS, GEMINI_MODELS)

# Gemini Config
GEN_CONFIG = {
    "temperature": 0.8,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 500,
}

SAFETY = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# Database Init
def init_db():
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            first_seen TEXT,
            last_seen TEXT,
            msg_count INTEGER DEFAULT 0,
            daily_count INTEGER DEFAULT 0,
            daily_reset TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )''')
        
        c.execute('CREATE INDEX IF NOT EXISTS idx_user ON chats(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_time ON chats(timestamp)')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"DB init error: {e}")

init_db()

# Database Functions
def get_user(user_id):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = c.fetchone()
        conn.close()
        return user
    except Exception as e:
        logger.error(f"get_user error: {e}")
        return None

def save_user(user_id):
    try:
        conn = get_db()
        c = conn.cursor()
        now = datetime.now().isoformat()
        today = datetime.now().date().isoformat()
        
        c.execute("SELECT daily_reset, daily_count FROM users WHERE user_id=?", (user_id,))
        user = c.fetchone()
        
        if user:
            daily_reset = user['daily_reset'] or today
            if daily_reset != today:
                c.execute(
                    "UPDATE users SET last_seen=?, msg_count=msg_count+1, daily_count=1, daily_reset=? WHERE user_id=?",
                    (now, today, user_id)
                )
            else:
                c.execute(
                    "UPDATE users SET last_seen=?, msg_count=msg_count+1, daily_count=daily_count+1 WHERE user_id=?",
                    (now, user_id)
                )
        else:
            c.execute(
                "INSERT INTO users (user_id, first_seen, last_seen, msg_count, daily_count, daily_reset) VALUES (?, ?, ?, 1, 1, ?)",
                (user_id, now, now, today)
            )
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"save_user error: {e}")

def check_daily_limit(user_id):
    try:
        user = get_user(user_id)
        if not user:
            return True
        today = datetime.now().date().isoformat()
        if user['daily_reset'] != today:
            return True
        return user['daily_count'] < MAX_DAILY_MESSAGES
    except:
        return True

def save_chat(user_id, role, content):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO chats (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, role, content, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"save_chat error: {e}")

def get_history(user_id, limit=6):
    try:
        conn = get_db()
        c = conn.cursor()
        
        cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
        c.execute("DELETE FROM chats WHERE user_id=? AND timestamp < ?", (user_id, cutoff))
        
        c.execute(
            "SELECT role, content FROM chats WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        )
        rows = c.fetchall()
        conn.commit()
        conn.close()
        return list(reversed(rows)) if rows else []
    except Exception as e:
        logger.error(f"get_history error: {e}")
        return []

# Text Processing
def clean_text(text):
    return re.sub(r'\s+', ' ', text.strip())

def detect_language(text):
    arabic = len(re.findall(r'[\u0600-\u06FF]', text))
    english = len(re.findall(r'[a-zA-Z]', text))
    return 'ar' if arabic > english else 'en'

# Commands
def get_help():
    return f"""📚 دليل الاستخدام

الأوامر:
• مساعدة - هذه الرسالة
• إعادة - مسح المحادثة
• معرفي - عرض معرفك
• إحصائياتي - إحصائياتك
• معلومات - عن البوت

الحدود:
• {MAX_DAILY_MESSAGES} رسالة/يوم
• {RATE_LIMIT_SECONDS}s بين الرسائل

{BOT_YEAR} © {BOT_CREATOR}"""

def get_welcome():
    return f"""👋 مرحبًا!

أنا {BOT_NAME} 🤖
مساعدك الذكي

ابدأ المحادثة الآن!
للمساعدة: اكتب "مساعدة"

{BOT_YEAR} © {BOT_CREATOR}"""

def get_info():
    return f"""ℹ️ {BOT_NAME} v{BOT_VERSION}

👩‍💻 {BOT_CREATOR}
📅 {BOT_YEAR}

⚙️ المواصفات:
• {len(GEMINI_KEYS)} API keys
• {len(GEMINI_MODELS)} models
• ذاكرة 48 ساعة

{MAX_DAILY_MESSAGES} رسالة/يوم"""

def get_stats(user_id):
    user = get_user(user_id)
    if not user:
        return "لم أجد بياناتك"
    
    today_count = user['daily_count']
    remaining = MAX_DAILY_MESSAGES - today_count
    
    return f"""📊 إحصائياتك

🆔 {user_id[:20]}...

📈 الاستخدام:
• إجمالي: {user['msg_count']}
• اليوم: {today_count}/{MAX_DAILY_MESSAGES}
• متبقي: {remaining}"""

# AI Engine
def generate_response(user_msg, user_id):
    lang = detect_language(user_msg)
    
    # Commands
    msg = user_msg.lower().strip()
    
    if msg in ['help', 'مساعدة', 'ساعدني']:
        return get_help()
    
    if msg in ['reset', 'إعادة', 'مسح']:
        try:
            conn = get_db()
            conn.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
            conn.commit()
            conn.close()
        except:
            pass
        return "✅ تم مسح المحادثة"
    
    if msg in ['id', 'معرفي', 'ايديي']:
        return f"🆔 معرفك:\n{user_id}"
    
    if msg in ['stats', 'إحصائياتي', 'حسابي']:
        return get_stats(user_id)
    
    if msg in ['info', 'معلومات', 'about']:
        return get_info()
    
    # Build context
    history = get_history(user_id, 4)
    context = ""
    if history:
        for h in history[-4:]:
            role = "المستخدم" if h['role'] == 'user' else "أنت"
            content = h['content'][:150]
            context += f"{role}: {content}\n"
    
    # Prompt
    system = "أنت مساعد ذكي. كن مختصرًا (1-3 جمل)." if lang == 'ar' else "You are a smart assistant. Be brief (1-3 sentences)."
    prompt = f"{system}\n\n{context}\nالسؤال: {user_msg}\nالرد:"
    
    # Try generate
    max_attempts = len(GEMINI_KEYS) * len(GEMINI_MODELS)
    
    for attempt in range(min(max_attempts, 6)):
        try:
            key, model, combo = model_manager.get_next()
            
            genai.configure(api_key=key)
            ai = genai.GenerativeModel(model, safety_settings=SAFETY, generation_config=GEN_CONFIG)
            
            response = ai.generate_content(prompt, request_options={"timeout": 15})
            
            if not response or not response.text:
                raise ValueError("Empty response")
            
            reply = clean_text(response.text)
            
            if len(reply) < 5:
                raise ValueError("Too short")
            
            if len(reply) > 1200:
                reply = reply[:1200] + "..."
            
            model_manager.mark_success(combo)
            return reply
            
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            model_manager.mark_fail(combo)
            
            if "safety" in str(e).lower():
                return "عذرًا، لا أستطيع الرد على هذا."
            
            if attempt < min(max_attempts, 6) - 1:
                time.sleep(0.2)
    
    return "عذرًا، حدث خطأ. حاول مرة أخرى."

# LINE Helpers
def send_push(user_id, text):
    try:
        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=text)]))
        return True
    except Exception as e:
        logger.error(f"Push failed: {e}")
        return False

# Async Worker
def process_message_async(user_id, user_msg):
    try:
        reply = generate_response(user_msg, user_id)
        save_chat(user_id, 'assistant', reply)
        send_push(user_id, reply)
    except Exception as e:
        logger.error(f"Async error: {e}")
        send_push(user_id, "عذرًا، حدث خطأ.")

def worker():
    while True:
        try:
            user_id, msg = message_queue.get()
            process_message_async(user_id, msg)
            message_queue.task_done()
        except Exception as e:
            logger.error(f"Worker error: {e}")

# Event Handlers
@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    save_user(user_id)
    send_push(user_id, get_welcome())

@handler.add(UnfollowEvent)
def handle_unfollow(event):
    pass

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    
    # Quick validation
    if not msg or len(msg) > 3000:
        return
    
    # Rate limit
    if not rate_limiter.is_allowed(user_id, RATE_LIMIT_SECONDS):
        return
    
    # Daily limit
    if not check_daily_limit(user_id):
        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"⚠️ وصلت للحد اليومي: {MAX_DAILY_MESSAGES} رسالة")]
                )
            )
        return
    
    # Save user and message
    save_user(user_id)
    save_chat(user_id, 'user', msg)
    
    # Queue for async processing
    message_queue.put((user_id, msg))
    
    # Quick reply
    ack = "🤔 جاري التفكير..." if detect_language(msg) == 'ar' else "🤔 Thinking..."
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(
            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=ack)])
        )

# Flask Routes
@app.route("/", methods=['GET'])
def home():
    return jsonify({"status": "running", "bot": BOT_NAME, "version": BOT_VERSION})

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        logger.error(f"Callback error: {e}")
    
    return 'OK'

@app.route("/health", methods=['GET'])
def health():
    return jsonify({"status": "healthy", "time": datetime.now().isoformat()})

# Start worker thread
worker_thread = threading.Thread(target=worker, daemon=True)
worker_thread.start()
logger.info("Worker thread started")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
