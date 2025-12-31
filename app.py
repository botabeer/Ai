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

# Gemini API Keys - يدعم حتى 6 مفاتيح
GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
    os.getenv("GEMINI_API_KEY_4"),
    os.getenv("GEMINI_API_KEY_5"),
    os.getenv("GEMINI_API_KEY_6"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

# Models - يدعم عدة مشغلات مع نظام التبديل التلقائي
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
BOT_VERSION = "2.4"
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

# Message Queue for async processing
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

# Smart Model & Key Manager
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
        logger.info(f"Total combinations: {len(self.stats)}")
    
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
                logger.warning(f"Marked fail for {combo_id}: {self.stats[combo_id]['fails']} fails")
    
    def mark_success(self, combo_id):
        with self.lock:
            if combo_id in self.stats:
                self.stats[combo_id]['success'] += 1
                self.stats[combo_id]['fails'] = max(0, self.stats[combo_id]['fails'] - 1)
    
    def get_stats(self):
        with self.lock:
            summary = {}
            for combo_id, stats in self.stats.items():
                summary[combo_id] = {
                    'model': stats['model'],
                    'key_num': stats['key_idx'] + 1,
                    'success': stats['success'],
                    'fails': stats['fails'],
                    'total': stats['total_requests']
                }
            return summary

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
        logger.info("Database initialized successfully")
        return True
    
    execute_db_query(_init)

logger.info("Initializing database...")
init_db()
logger.info("Database ready")

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
    return f"""دليل استخدام البوت 📚

الأوامر الأساسية:
• مساعدة / help - عرض هذه الرسالة
• إعادة / مسح - مسح المحادثة
• معرفي / ايديي - عرض معرفك
• إحصائياتي / حسابي - إحصائياتك
• معلومات / عن البوت - معلومات البوت

الحدود اليومية:
• {MAX_DAILY_MESSAGES} رسالة يوميًا
• {RATE_LIMIT_SECONDS} ثانية بين الرسائل

{BOT_YEAR} © {BOT_CREATOR}"""

def get_welcome_message():
    return f"""مرحبًا بك! 👋

أنا {BOT_NAME} - مساعدك الذكي 🤖

ماذا أستطيع أن أفعل؟
✓ الإجابة على أسئلتك
✓ النقاش في أي موضوع
✓ تقديم النصائح والمعلومات
✓ مساعدتك في حل المشاكل

ابدأ المحادثة الآن! 💬

للمساعدة: اكتب "مساعدة"

{BOT_YEAR} © {BOT_CREATOR}"""

def get_bot_info():
    return f"""معلومات البوت ℹ️

📱 الاسم: {BOT_NAME}
🔢 الإصدار: v{BOT_VERSION}
👩‍💻 المطورة: {BOT_CREATOR}
📅 السنة: {BOT_YEAR}

⚙️ المواصفات:
• دعم العربية والإنجليزية
• ذاكرة 48 ساعة
• {len(GEMINI_KEYS)} مفاتيح API
• {len(GEMINI_MODELS)} مشغلات AI
• نظام تبديل ذكي تلقائي

📊 الحدود:
• {MAX_DAILY_MESSAGES} رسالة/يوم
• {RATE_LIMIT_SECONDS} ثانية بين الرسائل

🤖 المشغلات المتوفرة:
{chr(10).join(f'• {model}' for model in GEMINI_MODELS)}

{BOT_YEAR} © {BOT_CREATOR}"""

def get_user_stats(user_id):
    user = get_user(user_id)
    if not user:
        return "لم أجد بياناتك. جرب إرسال رسالة أولًا."
    
    first_seen = datetime.fromisoformat(user['first_seen'])
    days_active = (datetime.now() - first_seen).days
    today_count = user['daily_count']
    remaining = MAX_DAILY_MESSAGES - today_count
    
    return f"""إحصائياتك الشخصية 📊

🆔 معرف حسابك:
{user_id}

📈 الاستخدام:
• إجمالي الرسائل: {user['msg_count']}
• رسائل اليوم: {today_count}/{MAX_DAILY_MESSAGES}
• متبقي اليوم: {remaining} رسالة

⏰ النشاط:
• أول استخدام: {first_seen.strftime('%Y-%m-%d')}
• آخر نشاط: {datetime.fromisoformat(user['last_seen']).strftime('%Y-%m-%d %H:%M')}
• الأيام النشطة: {days_active} يوم

{BOT_YEAR} © {BOT_CREATOR}"""

# AI Engine
def generate_response(user_msg, user_id):
    lang = detect_language(user_msg)
    history = get_history(user_id, limit=8)
    context = ""
    
    if history:
        context = "\n## السياق السابق:\n"
        for msg in history[-6:]:
            role = "المستخدم" if msg['role'] == 'user' else "أنت"
            content = msg['content'][:200] if len(msg['content']) > 200 else msg['content']
            context += f"{role}: {content}\n"
    
    # Check for commands
    msg_lower = user_msg.lower().strip()
    
    if msg_lower in ['start', 'help', 'مساعدة', 'ساعدني', 'الأوامر', 'أوامر']:
        return get_help_message()
    
    if msg_lower in ['reset', 'إعادة', 'مسح', 'ابدأ من جديد', 'حذف المحادثة']:
        def _reset():
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
            conn.commit()
            conn.close()
            return True
        execute_db_query(_reset)
        return "تم مسح المحادثة بنجاح ✅\nلنبدأ محادثة جديدة 🆕"
    
    if msg_lower in ['id', 'معرفي', 'ايديي', 'معرف']:
        return f"🆔 معرف حسابك في LINE:\n\n{user_id}\n\n{BOT_YEAR} © {BOT_CREATOR}"
    
    if msg_lower in ['stats', 'إحصائياتي', 'احصائياتي', 'حسابي']:
        return get_user_stats(user_id)
    
    if msg_lower in ['info', 'معلومات', 'عن البوت', 'about']:
        return get_bot_info()
    
    # Build system prompt
    if lang == 'ar':
        system_prompt = """أنت مساعد ذكي محترف يتحدث العربية.

## شخصيتك:
- ذكي ومحترف
- واضح ومباشر
- ودود احترافي
- مختصر وفعال

## قواعد الرد:
- كن مختصرًا (1-3 جمل للأسئلة البسيطة)
- للمواضيع المعقدة: نقاط قصيرة
- لغة طبيعية بسيطة
- ركز على الإجابة المفيدة
- كن دقيقًا"""
    else:
        system_prompt = """You are a smart, professional AI assistant.

## Personality:
- Intelligent and professional
- Clear and direct
- Friendly but professional
- Concise and effective

## Response rules:
- Be brief (1-3 sentences for simple questions)
- For complex topics: bullet points
- Natural language
- Focus on useful answers
- Be accurate"""
    
    prompt = f"""{system_prompt}

{context}

## السؤال الحالي:
{user_msg}

## ردك (واضح ومختصر):"""

    # Try with multiple model/key combinations
    max_attempts = len(GEMINI_KEYS) * len(GEMINI_MODELS)
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            current_key, current_model, combo_id = model_manager.get_best_combo()
            logger.info(f"Attempt {attempt + 1}/{max_attempts}: Using {combo_id} - {current_model}")
            
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel(
                current_model,
                safety_settings=SAFETY,
                generation_config=GEN_CONFIG
            )
            
            response = model.generate_content(prompt, request_options={"timeout": 20})
            
            if not response or not hasattr(response, 'text') or not response.text:
                if hasattr(response, 'prompt_feedback'):
                    logger.warning(f"Response blocked: {response.prompt_feedback}")
                raise ValueError("Empty response from API")
            
            reply = clean_text(response.text.strip())
            logger.info(f"Got response: {len(reply)} chars from {current_model}")
            
            if len(reply) < 5:
                raise ValueError("Response too short")
            
            if len(reply) > 1500:
                sentences = reply.split('.')
                reply = '.'.join(sentences[:5]) + '.'
            
            tokens = estimate_tokens(reply)
            model_manager.mark_success(combo_id)
            log_event('response_generated', user_id, {'tokens': tokens, 'model': current_model})
            
            return reply
            
        except Exception as e:
            last_error = str(e)
            error_lower = str(e).lower()
            logger.error(f"Attempt {attempt + 1} failed with {combo_id}: {e}")
            
            is_quota = "quota" in error_lower or "resource" in error_lower or "429" in error_lower
            model_manager.mark_fail(combo_id, is_quota)
            
            if "safety" in error_lower or "block" in error_lower:
                log_event('safety_block', user_id)
                return "عذرًا، لا أستطيع الرد على هذا الموضوع. دعنا نتحدث عن شيء آخر."
            
            if attempt < max_attempts - 1:
                time.sleep(0.3)
                continue
    
    logger.error(f"All attempts failed. Last error: {last_error}")
    log_event('generation_failed', user_id, {'error': last_error})
    
    if "quota" in str(last_error).lower():
        return "عذرًا، وصلنا للحد الأقصى. حاول مرة أخرى بعد دقيقة."
    return "عذرًا، حدث خطأ تقني. حاول مرة أخرى.\n\nللدعم: أرسل 'معرفي'"

# LINE Helper Functions
def send_loading_animation(user_id):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.show_loading_animation(
                ShowLoadingAnimationRequest(chatId=user_id, loadingSeconds=5)
            )
    except Exception as e:
        logger.debug(f"Could not send loading animation: {e}")

def send_push_message(user_id, text):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[TextMessage(text=text)])
            )
        return True
    except Exception as e:
        logger.error(f"Failed to send push message: {e}")
        return False

# Async Message Processor
def process_message_async(user_id, user_msg):
    try:
        logger.info(f"Processing message async for {user_id}")
        bot_reply = generate_response(user_msg, user_id)
        save_chat(user_id, 'assistant', bot_reply)
        send_push_message(user_id, bot_reply)
        log_event('message_sent', user_id)
        logger.info(f"Successfully sent async reply to {user_id}")
    except Exception as e:
        logger.error(f"Failed to process async message: {e}", exc_info=True)
        send_push_message(user_id, "عذرًا، حدث خطأ. حاول مرة أخرى.")

def message_worker():
    while True:
        try:
            user_id, user_msg = message_queue.get()
            process_message_async(user_id, user_msg)
            message_queue.task_done()
        except Exception as e:
            logger.error(f"Message worker error: {e}")

# LINE Event Handlers
@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    save_user(user_id)
    log_event('user_follow', user_id)
    
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[TextMessage(text=get_welcome_message())])
            )
    except Exception as e:
        logger.error(f"Failed to send welcome: {e}")

@handler.add(UnfollowEvent)
def handle_unfollow(event):
    log_event('user_unfollow', event.source.user_id)

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()
    
    logger.info(f"Received message from {user_id}: {user_msg[:50]}...")
    
    if not user_msg:
        return
    
    # Check message length
    if len(user_msg) > 3000:
        reply = f"⚠️ الرسالة طويلة جدًا\n\nالحد الأقصى: 3000 حرف\nرسالتك: {len(user_msg)} حرف"
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
                )
        except Exception as e:
            logger.error(f"Failed to send limit error: {e}")
        return
    
    # Save user and message
    save_user(user_id)
    save_chat(user_id, 'user', user_msg)
    
    # Send loading animation
    send_loading_animation(user_id)
    
    # Add message to queue for async processing
    message_queue.put((user_id, user_msg))
    
    # Send immediate acknowledgment
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            ack_msg = "جاري التفكير... 🤔" if detect_language(user_msg) == 'ar' else "Thinking... 🤔"
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=ack_msg)])
            )
    except Exception as e:
        logger.error(f"Failed to send acknowledgment: {e}")

# Flask Routes
@app.route("/", methods=['GET'])
def home():
    return jsonify({
        "status": "running",
        "bot": BOT_NAME,
        "version": BOT_VERSION,
        "creator": BOT_CREATOR,
        "year": BOT_YEAR
    })

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Error handling webhook: {e}", exc_info=True)
    
    return 'OK'

@app.route("/health", methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })

@app.route("/stats", methods=['GET'])
def stats():
    auth_header = request.headers.get('Authorization')
    if auth_header != f"Bearer {ADMIN_USER_ID}":
        abort(401)
    
    def _get_stats():
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) as total FROM users")
        total_users = c.fetchone()['total']
        
        c.execute("SELECT COUNT(*) as total FROM users WHERE daily_reset = ?", 
                  (datetime.now().date().isoformat(),))
        active_today = c.fetchone()['total']
        
        c.execute("SELECT COUNT(*) as total FROM chats")
        total_messages = c.fetchone()['total']
        
        conn.close()
        return {
            'total_users': total_users,
            'active_today': active_today,
            'total_messages': total_messages,
            'model_stats': model_manager.get_stats()
        }
    
    result = execute_db_query(_get_stats)
    return jsonify(result if result else {"error": "Failed to get stats"})

@app.route("/clean", methods=['POST'])
def clean():
    auth_header = request.headers.get('Authorization')
    if auth_header != f"Bearer {ADMIN_USER_ID}":
        abort(401)
    
    clean_old_data()
    return jsonify({"status": "cleaned"})

# Start worker thread
worker_thread = threading.Thread(target=message_worker, daemon=True)
worker_thread.start()
logger.info("Message worker thread started")

# Clean old data on startup
clean_old_data()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
                )
        except Exception as e:
            logger.error(f"Failed to send error: {e}")
        return
    
    # Check rate limit
    if not rate_limiter.is_allowed(user_id, RATE_LIMIT_SECONDS):
        logger.info(f"Rate limit hit for {user_id}")
        return
    
    # Check daily limit
    if not check_daily_limit(user_id):
        reply = f"⚠️ وصلت للحد اليومي\n\nالحد: {MAX_DAILY_MESSAGES} رسالة/يوم\nيمكنك المتابعة غدًا 🔄"
        try:
            with ApiClient(configuration) as api_client:
