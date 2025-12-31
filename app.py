import os
import sqlite3
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
import google.generativeai as genai
import logging
import time
import threading
import re
from functools import wraps
from collections import defaultdict
from queue import Queue

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

# Configuration
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# Gemini API Keys
GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
    os.getenv("GEMINI_API_KEY_4"),
    os.getenv("GEMINI_API_KEY_5"),
    os.getenv("GEMINI_API_KEY_6"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

# Models
GEMINI_MODELS = [
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
]

# Settings
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "")
MAX_DAILY_MESSAGES = int(os.getenv("MAX_DAILY_MESSAGES", "100"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "2"))

# Bot Info
BOT_NAME = "Smart Assistant"
BOT_VERSION = "2.5"
BOT_CREATOR = "عبير الدوسري"
BOT_YEAR = "2025"

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise ValueError("Missing LINE credentials")

if not GEMINI_KEYS:
    raise ValueError("Missing Gemini API keys")

# LINE v3 Setup
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Database
DB_PATH = "chatbot.db"
DB_TIMEOUT = 30.0
db_lock = threading.Lock()

# Message Queue
message_queue = Queue()

# Database Helper Functions
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.row_factory = sqlite3.Row
    return conn

def execute_db_query(query_func, max_retries=3):
    for attempt in range(max_retries):
        try:
            with db_lock:
                return query_func()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
                continue
            logger.error(f"Database error after {attempt + 1} attempts: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected database error: {e}")
            return None
    return None

# Rate Limiter
class RateLimiter:
    def __init__(self):
        self.user_requests = defaultdict(list)
        self.lock = threading.Lock()
    
    def is_allowed(self, user_id, seconds=2):
        with self.lock:
            now = time.time()
            self.user_requests[user_id] = [
                t for t in self.user_requests[user_id] 
                if now - t < 60
            ]
            
            if self.user_requests[user_id]:
                if now - self.user_requests[user_id][-1] < seconds:
                    return False
            
            self.user_requests[user_id].append(now)
            return True

rate_limiter = RateLimiter()

# Smart Model Manager
class SmartModelManager:
    def __init__(self, keys, models):
        self.keys = keys
        self.models = models
        self.stats = {}
        
        for key_idx, key in enumerate(keys):
            for model_idx, model in enumerate(models):
                combo_id = f"key{key_idx+1}_model{model_idx+1}"
                self.stats[combo_id] = {
                    'key': key,
                    'model': model,
                    'key_idx': key_idx,
                    'model_idx': model_idx,
                    'fails': 0,
                    'success': 0,
                    'last_fail': 0,
                    'total_requests': 0,
                    'quota_reset': 0
                }
        
        self.lock = threading.Lock()
        logger.info(f"Initialized with {len(keys)} API keys and {len(models)} models")
    
    def get_best_combo(self):
        with self.lock:
            best_combo = None
            min_fails = float('inf')
            
            for combo_id, stats in self.stats.items():
                if stats['last_fail'] and (time.time() - stats['last_fail']) > 900:
                    stats['fails'] = 0
                    stats['last_fail'] = 0
                
                if stats['quota_reset'] and (time.time() - stats['quota_reset']) > 3600:
                    stats['fails'] = 0
                    stats['quota_reset'] = 0
                
                if stats['fails'] < min_fails:
                    min_fails = stats['fails']
                    best_combo = combo_id
            
            if best_combo:
                self.stats[best_combo]['total_requests'] += 1
                return self.stats[best_combo]['key'], self.stats[best_combo]['model'], best_combo
            
            first_combo = list(self.stats.keys())[0]
            return self.stats[first_combo]['key'], self.stats[first_combo]['model'], first_combo
    
    def mark_fail(self, combo_id, is_quota=False):
        with self.lock:
            if combo_id in self.stats:
                self.stats[combo_id]['fails'] += 1
                self.stats[combo_id]['last_fail'] = time.time()
                if is_quota:
                    self.stats[combo_id]['quota_reset'] = time.time()
    
    def mark_success(self, combo_id):
        with self.lock:
            if combo_id in self.stats:
                self.stats[combo_id]['success'] += 1
                self.stats[combo_id]['fails'] = max(0, self.stats[combo_id]['fails'] - 1)
    
    def get_stats(self):
        with self.lock:
            return {
                combo_id: {
                    'model': stats['model'],
                    'key_num': stats['key_idx'] + 1,
                    'success': stats['success'],
                    'fails': stats['fails'],
                    'total': stats['total_requests']
                }
                for combo_id, stats in self.stats.items()
            }

model_manager = SmartModelManager(GEMINI_KEYS, GEMINI_MODELS)

# Gemini Config
GEN_CONFIG = {
    "temperature": 0.8,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 600,
}

SAFETY = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# Database Functions
def init_db():
    def _init():
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            first_seen TEXT,
            last_seen TEXT,
            msg_count INTEGER DEFAULT 0,
            daily_count INTEGER DEFAULT 0,
            daily_reset TEXT,
            is_blocked INTEGER DEFAULT 0,
            language TEXT DEFAULT 'ar'
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            tokens INTEGER DEFAULT 0,
            timestamp TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            user_id TEXT,
            data TEXT,
            timestamp TEXT
        )''')
        
        c.execute('CREATE INDEX IF NOT EXISTS idx_user ON chats(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_time ON chats(timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_analytics ON analytics(user_id, timestamp)')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized")
        return True
    
    execute_db_query(_init)

init_db()

def log_event(event_type, user_id=None, data=None):
    def _log():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO analytics (event_type, user_id, data, timestamp) VALUES (?, ?, ?, ?)",
            (event_type, user_id, str(data) if data else None, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        return True
    execute_db_query(_log)

def get_user(user_id):
    def _get():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = c.fetchone()
        conn.close()
        return user
    return execute_db_query(_get)

def save_user(user_id, name=None):
    def _save():
        conn = get_db_connection()
        c = conn.cursor()
        now = datetime.now().isoformat()
        today = datetime.now().date().isoformat()
        
        user = get_user(user_id)
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
                "INSERT INTO users (user_id, name, first_seen, last_seen, msg_count, daily_count, daily_reset) VALUES (?, ?, ?, ?, 1, 1, ?)",
                (user_id, name, now, now, today)
            )
            log_event('new_user', user_id)
        
        conn.commit()
        conn.close()
        return True
    execute_db_query(_save)

def check_daily_limit(user_id):
    user = get_user(user_id)
    if not user:
        return True
    today = datetime.now().date().isoformat()
    if user['daily_reset'] != today:
        return True
    return user['daily_count'] < MAX_DAILY_MESSAGES

def save_chat(user_id, role, content, tokens=0):
    def _save():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO chats (user_id, role, content, tokens, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, role, content, tokens, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        return True
    execute_db_query(_save)

def get_history(user_id, limit=8):
    def _get():
        conn = get_db_connection()
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
    
    result = execute_db_query(_get)
    return result if result is not None else []

def clean_old_data():
    def _clean():
        conn = get_db_connection()
        c = conn.cursor()
        
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        c.execute("DELETE FROM chats WHERE timestamp < ?", (week_ago,))
        
        month_ago = (datetime.now() - timedelta(days=30)).isoformat()
        c.execute("DELETE FROM analytics WHERE timestamp < ?", (month_ago,))
        
        conn.commit()
        conn.close()
        logger.info("Cleaned old data")
        return True
    execute_db_query(_clean)

# Text Processing
def clean_text(text):
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    return text

def detect_language(text):
    arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
    english_chars = len(re.findall(r'[a-zA-Z]', text))
    return 'ar' if arabic_chars > english_chars else 'en'

def estimate_tokens(text):
    return int(len(text.split()) * 1.3)

# Commands
def get_help_message():
    return f"""📚 دليل استخدام البوت

الأوامر الأساسية:
• مساعدة / help
• إعادة / مسح
• معرفي / ايديي
• إحصائياتي / حسابي
• معلومات / عن البوت

الحدود:
• {MAX_DAILY_MESSAGES} رسالة/يوم
• {RATE_LIMIT_SECONDS} ثانية بين الرسائل

{BOT_YEAR} © {BOT_CREATOR}"""

def get_welcome_message():
    return f"""👋 مرحبًا بك!

أنا {BOT_NAME} 🤖

ماذا أستطيع أن أفعل؟
✓ الإجابة على أسئلتك
✓ النقاش في أي موضوع
✓ تقديم النصائح
✓ المساعدة في حل المشاكل

ابدأ المحادثة الآن! 💬

{BOT_YEAR} © {BOT_CREATOR}"""

def get_bot_info():
    return f"""ℹ️ معلومات البوت

📱 {BOT_NAME} v{BOT_VERSION}
👩‍💻 {BOT_CREATOR}
📅 {BOT_YEAR}

⚙️ المواصفات:
• {len(GEMINI_KEYS)} API keys
• {len(GEMINI_MODELS)} AI models
• نظام تبديل ذكي
• ذاكرة 48 ساعة

📊 الحدود:
• {MAX_DAILY_MESSAGES} رسالة/يوم
• {RATE_LIMIT_SECONDS}s بين الرسائل"""

def get_user_stats(user_id):
    user = get_user(user_id)
    if not user:
        return "لم أجد بياناتك. أرسل رسالة أولاً."
    
    first_seen = datetime.fromisoformat(user['first_seen'])
    days_active = (datetime.now() - first_seen).days
    today_count = user['daily_count']
    remaining = MAX_DAILY_MESSAGES - today_count
    
    return f"""📊 إحصائياتك

🆔 {user_id}

📈 الاستخدام:
• إجمالي: {user['msg_count']}
• اليوم: {today_count}/{MAX_DAILY_MESSAGES}
• متبقي: {remaining}

⏰ النشاط:
• منذ: {first_seen.strftime('%Y-%m-%d')}
• آخر نشاط: {datetime.fromisoformat(user['last_seen']).strftime('%H:%M')}
• الأيام: {days_active}"""

# AI Engine
def generate_response(user_msg, user_id):
    lang = detect_language(user_msg)
    history = get_history(user_id, limit=8)
    
    # Check commands
    msg_lower = user_msg.lower().strip()
    
    if msg_lower in ['start', 'help', 'مساعدة', 'ساعدني', 'الأوامر']:
        return get_help_message()
    
    if msg_lower in ['reset', 'إعادة', 'مسح', 'ابدأ من جديد']:
        def _reset():
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
            conn.commit()
            conn.close()
            return True
        execute_db_query(_reset)
        return "✅ تم مسح المحادثة\n🆕 لنبدأ محادثة جديدة"
    
    if msg_lower in ['id', 'معرفي', 'ايديي', 'معرف']:
        return f"🆔 معرفك:\n{user_id}"
    
    if msg_lower in ['stats', 'إحصائياتي', 'احصائياتي', 'حسابي']:
        return get_user_stats(user_id)
    
    if msg_lower in ['info', 'معلومات', 'عن البوت', 'about']:
        return get_bot_info()
    
    # Build context
    context = ""
    if history:
        context = "\n## السياق:\n"
        for msg in history[-6:]:
            role = "المستخدم" if msg['role'] == 'user' else "أنت"
            content = msg['content'][:200]
            context += f"{role}: {content}\n"
    
    # System prompt
    if lang == 'ar':
        system_prompt = """أنت مساعد ذكي محترف.

قواعد الرد:
- مختصر (1-3 جمل)
- واضح ومباشر
- ودود احترافي
- دقيق ومفيد"""
    else:
        system_prompt = """You are a smart assistant.

Rules:
- Brief (1-3 sentences)
- Clear and direct
- Friendly professional
- Accurate and helpful"""
    
    prompt = f"{system_prompt}\n{context}\n## السؤال:\n{user_msg}\n## الرد:"
    
    # Try with multiple combinations
    max_attempts = len(GEMINI_KEYS) * len(GEMINI_MODELS)
    
    for attempt in range(max_attempts):
        try:
            current_key, current_model, combo_id = model_manager.get_best_combo()
            logger.info(f"Try {attempt + 1}: {combo_id}")
            
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel(
                current_model,
                safety_settings=SAFETY,
                generation_config=GEN_CONFIG
            )
            
            response = model.generate_content(prompt, request_options={"timeout": 20})
            
            if not response or not hasattr(response, 'text') or not response.text:
                raise ValueError("Empty response")
            
            reply = clean_text(response.text.strip())
            
            if len(reply) < 5:
                raise ValueError("Too short")
            
            if len(reply) > 1500:
                sentences = reply.split('.')
                reply = '.'.join(sentences[:5]) + '.'
            
            tokens = estimate_tokens(reply)
            model_manager.mark_success(combo_id)
            log_event('response_generated', user_id, {'tokens': tokens})
            
            return reply
            
        except Exception as e:
            error_lower = str(e).lower()
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            
            is_quota = "quota" in error_lower or "429" in error_lower
            model_manager.mark_fail(combo_id, is_quota)
            
            if "safety" in error_lower or "block" in error_lower:
                return "عذرًا، لا أستطيع الرد على هذا. دعنا نتحدث عن شيء آخر."
            
            if attempt < max_attempts - 1:
                time.sleep(0.3)
    
    return "عذرًا، حدث خطأ تقني. حاول مرة أخرى."

# LINE Helpers
def send_loading_animation(user_id):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.show_loading_animation(
                ShowLoadingAnimationRequest(chatId=user_id, loadingSeconds=5)
            )
    except Exception as e:
        logger.debug(f"Loading animation failed: {e}")

def send_push_message(user_id, text):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[TextMessage(text=text)])
            )
        return True
    except Exception as e:
        logger.error(f"Push message failed: {e}")
        return False

# Async Processor
def process_message_async(user_id, user_msg):
    try:
        bot_reply = generate_response(user_msg, user_id)
        save_chat(user_id, 'assistant', bot_reply)
        send_push_message(user_id, bot_reply)
        log_event('message_sent', user_id)
    except Exception as e:
        logger.error(f"Async processing failed: {e}")
        send_push_message(user_id, "عذرًا، حدث خطأ.")

def message_worker():
    while True:
        try:
            user_id, user_msg = message_queue.get()
            process_message_async(user_id, user_msg)
            message_queue.task_done()
        except Exception as e:
            logger.error(f"Worker error: {e}")

# Event Handlers
@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    save_user(user_id)
    log_event('user_follow', user_id)
    send_push_message(user_id, get_welcome_message())

@handler.add(UnfollowEvent)
def handle_unfollow(event):
    log_event('user_unfollow', event.source.user_id)

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()
    
    if not user_msg or len(user_msg) > 3000:
        return
    
    if not rate_limiter.is_allowed(user_id, RATE_LIMIT_SECONDS):
        return
    
    if not check_daily_limit(user_id):
        reply = f"⚠️ وصلت للحد اليومي\n{MAX_DAILY_MESSAGES} رسالة/يوم"
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
            )
        return
    
    save_user(user_id)
    save_chat(user_id, 'user', user_msg)
    send_loading_animation(user_id)
    message_queue.put((user_id, user_msg))
    
    ack = "🤔 جاري التفكير..." if detect_language(user_msg) == 'ar' else "🤔 Thinking..."
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=ack)])
        )

# Flask Routes
@app.route("/", methods=['GET'])
def home():
    return jsonify({
        "status": "running",
        "bot": BOT_NAME,
        "version": BOT_VERSION,
        "creator": BOT_CREATOR
    })

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    
    return 'OK'

@app.route("/health", methods=['GET'])
def health():
    return jsonify({"status": "healthy"})

@app.route("/stats", methods=['GET'])
def stats():
    auth = request.headers.get('Authorization')
    if auth != f"Bearer {ADMIN_USER_ID}":
        abort(401)
    
    def _stats():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as total FROM users")
        total = c.fetchone()['total']
        c.execute("SELECT COUNT(*) as total FROM chats")
        msgs = c.fetchone()['total']
        conn.close()
        return {'users': total, 'messages': msgs, 'models': model_manager.get_stats()}
    
    return jsonify(execute_db_query(_stats))

@app.route("/clean", methods=['POST'])
def clean():
    auth = request.headers.get('Authorization')
    if auth != f"Bearer {ADMIN_USER_ID}":
        abort(401)
    clean_old_data()
    return jsonify({"status": "cleaned"})

# Start worker
worker = threading.Thread(target=message_worker, daemon=True)
worker.start()
clean_old_data()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
