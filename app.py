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

GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3")
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

# Settings
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "")
MAX_DAILY_MESSAGES = int(os.getenv("MAX_DAILY_MESSAGES", "100"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "2"))

# Bot Info
BOT_NAME = "Smart Assistant"
BOT_VERSION = "2.0"
BOT_CREATOR = "عبير الدوسري"
BOT_YEAR = "2025"

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise ValueError("Missing LINE credentials")

if not GEMINI_KEYS:
    raise ValueError("Missing Gemini API keys")

# LINE v3 Setup
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

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

# Smart API Key Manager
class SmartKeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.index = 0
        self.stats = {
            k: {
                'fails': 0, 
                'success': 0, 
                'last_fail': 0,
                'total_requests': 0,
                'quota_reset': 0
            } for k in keys
        }
        self.lock = threading.Lock()
        logger.info(f"Initialized with {len(keys)} API keys")
    
    def get_key(self):
        with self.lock:
            best_key = None
            min_fails = float('inf')
            
            for key in self.keys:
                stats = self.stats[key]
                
                if stats['last_fail'] and (time.time() - stats['last_fail']) > 900:
                    stats['fails'] = 0
                    stats['last_fail'] = 0
                
                if stats['quota_reset'] and (time.time() - stats['quota_reset']) > 3600:
                    stats['fails'] = 0
                    stats['quota_reset'] = 0
                
                if stats['fails'] < min_fails:
                    min_fails = stats['fails']
                    best_key = key
            
            if best_key:
                self.stats[best_key]['total_requests'] += 1
            
            return best_key or self.keys[0]
    
    def mark_fail(self, key, is_quota=False):
        with self.lock:
            if key in self.stats:
                self.stats[key]['fails'] += 1
                self.stats[key]['last_fail'] = time.time()
                if is_quota:
                    self.stats[key]['quota_reset'] = time.time()
    
    def mark_success(self, key):
        with self.lock:
            if key in self.stats:
                self.stats[key]['success'] += 1
                self.stats[key]['fails'] = max(0, self.stats[key]['fails'] - 1)
    
    def get_stats(self):
        with self.lock:
            return {
                f"key_{i+1}": {
                    'success': stats['success'],
                    'fails': stats['fails'],
                    'total': stats['total_requests']
                }
                for i, (key, stats) in enumerate(self.stats.items())
            }

key_manager = SmartKeyManager(GEMINI_KEYS)

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

# Database
DB_PATH = "chatbot.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
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
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

def log_event(event_type, user_id=None, data=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO analytics (event_type, user_id, data, timestamp) VALUES (?, ?, ?, ?)",
            (event_type, user_id, str(data), datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log event: {e}")

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def save_user(user_id, name=None):
    conn = sqlite3.connect(DB_PATH)
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

def check_daily_limit(user_id):
    user = get_user(user_id)
    if not user:
        return True
    
    today = datetime.now().date().isoformat()
    if user['daily_reset'] != today:
        return True
    
    return user['daily_count'] < MAX_DAILY_MESSAGES

def save_chat(user_id, role, content, tokens=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO chats (user_id, role, content, tokens, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, role, content, tokens, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_history(user_id, limit=8):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    
    return list(reversed(rows))

def clean_old_data():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        c.execute("DELETE FROM chats WHERE timestamp < ?", (week_ago,))
        
        month_ago = (datetime.now() - timedelta(days=30)).isoformat()
        c.execute("DELETE FROM analytics WHERE timestamp < ?", (month_ago,))
        
        conn.commit()
        conn.close()
        logger.info("Cleaned old data")
    except Exception as e:
        logger.error(f"Failed to clean data: {e}")

# Text Processing
def clean_text(text):
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE
    )
    text = emoji_pattern.sub('', text).strip()
    text = re.sub(r'\s+', ' ', text)
    return text

def detect_language(text):
    arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
    english_chars = len(re.findall(r'[a-zA-Z]', text))
    
    if arabic_chars > english_chars:
        return 'ar'
    return 'en'

def estimate_tokens(text):
    return len(text.split()) * 1.3

# Commands
def get_help_message():
    return f"""دليل استخدام البوت

الأوامر الأساسية:

• مساعدة أو help - عرض هذه الرسالة
• إعادة أو مسح - مسح المحادثة والبدء من جديد
• معرفي أو ايديي - عرض معرف حسابك
• إحصائياتي أو حسابي - عرض إحصائياتك
• معلومات أو عن البوت - معلومات عن البوت

القواعد:
1. اكتب بشكل طبيعي
2. كن واضحاً في سؤالك
3. انتظر {RATE_LIMIT_SECONDS} ثانية بين الرسائل
4. استخدم "إعادة" عند تغيير الموضوع

الحدود اليومية:
• {MAX_DAILY_MESSAGES} رسالة يومياً
• {RATE_LIMIT_SECONDS} ثانية بين الرسائل

{BOT_YEAR} - تم الإنشاء بواسطة {BOT_CREATOR}"""

def get_welcome_message():
    return f"""مرحباً بك

أنا {BOT_NAME} - مساعدك الذكي

ماذا أستطيع أن أفعل؟
• الإجابة على أسئلتك
• النقاش في أي موضوع
• تقديم النصائح والمعلومات
• مساعدتك في حل المشاكل

ابدأ المحادثة:
اكتب أي شيء وسأساعدك فوراً

للمساعدة: اكتب /help أو مساعدة

{BOT_YEAR} - تم الإنشاء بواسطة {BOT_CREATOR}"""

def get_bot_info():
    return f"""معلومات البوت

الاسم: {BOT_NAME}
الإصدار: v{BOT_VERSION}
المطورة: {BOT_CREATOR}
السنة: {BOT_YEAR}

المواصفات:
• يدعم اللغة العربية والإنجليزية
• ذاكرة محادثة ذكية (48 ساعة)
• نظام حماية متقدم
• {len(GEMINI_KEYS)} مفاتيح API للأداء العالي

الحدود:
• {MAX_DAILY_MESSAGES} رسالة يومياً
• {RATE_LIMIT_SECONDS} ثانية بين الرسائل

التقنيات:
• LINE Bot SDK v3
• Google Gemini 2.0 AI
• Python + Flask

{BOT_YEAR} - جميع الحقوق محفوظة
تم الإنشاء بواسطة {BOT_CREATOR}"""

def get_user_stats(user_id):
    user = get_user(user_id)
    if not user:
        return "لم أجد بياناتك. جرب إرسال رسالة أولاً."
    
    first_seen = datetime.fromisoformat(user['first_seen'])
    days_active = (datetime.now() - first_seen).days
    
    today_count = user['daily_count']
    remaining = MAX_DAILY_MESSAGES - today_count
    
    return f"""إحصائياتك الشخصية

معرف حسابك:
{user_id}

الاستخدام:
• إجمالي الرسائل: {user['msg_count']}
• رسائل اليوم: {today_count}/{MAX_DAILY_MESSAGES}
• متبقي اليوم: {remaining} رسالة

النشاط:
• أول استخدام: {first_seen.strftime('%Y-%m-%d')}
• آخر نشاط: {datetime.fromisoformat(user['last_seen']).strftime('%Y-%m-%d %H:%M')}
• عدد الأيام: {days_active} يوم

اللغة المفضلة: {'العربية' if user['language'] == 'ar' else 'English'}

{BOT_YEAR} - {BOT_CREATOR}"""

# AI Engine
def generate_response(user_msg, user_id):
    lang = detect_language(user_msg)
    history = get_history(user_id, limit=8)
    context = ""
    
    if history:
        context = "\n## السياق السابق:\n"
        for msg in history[-6:]:
            role = "المستخدم" if msg['role'] == 'user' else "أنت"
            context += f"{role}: {msg['content'][:200]}\n"
    
    # Check for commands
    msg_lower = user_msg.lower().strip()
    
    # Help commands
    if msg_lower in ['start', 'help', 'مساعدة', 'ساعدني', 'الأوامر', 'أوامر', 'ساعد', 'مساعده']:
        return get_help_message()
    
    # Reset commands
    if msg_lower in ['reset', 'إعادة', 'مسح', 'ابدأ من جديد', 'حذف المحادثة', 'اعادة', 'مسح المحادثة', 'ابدا من جديد', 'بداية جديدة']:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        return "تم مسح المحادثة بنجاح\nلنبدأ محادثة جديدة"
    
    # ID commands
    if msg_lower in ['id', 'معرفي', 'ايديي', 'user id', 'my id', 'معرف', 'معرفي ايش', 'وش معرفي']:
        return f"""معرف حسابك في LINE:

{user_id}

يمكنك استخدام هذا المعرف للدعم الفني أو الإبلاغ عن مشاكل.

{BOT_YEAR} - {BOT_CREATOR}"""
    
    # Stats commands
    if msg_lower in ['stats', 'إحصائياتي', 'احصائياتي', 'حسابي', 'بياناتي', 'احصائيات', 'إحصائيات', 'معلوماتي']:
        return get_user_stats(user_id)
    
    # Info commands
    if msg_lower in ['info', 'معلومات', 'عن البوت', 'about', 'معلومات البوت', 'من أنت', 'من انت', 'وش البوت']:
        return get_bot_info()
    
    # Build system prompt
    if lang == 'ar':
        system_prompt = """أنت مساعد ذكي محترف يتحدث العربية، أسلوبك مشابه لـ ChatGPT.

## شخصيتك:
- ذكي ومحترف
- واضح ومباشر
- ودود لكن احترافي
- مختصر وفعّال

## قواعد الرد:
- كن مختصراً جداً (1-3 جمل للأسئلة البسيطة)
- للمواضيع المعقدة: استخدم نقاط أو فقرات قصيرة
- لا تستخدم إيموجي إلا نادراً
- استخدم لغة طبيعية وبسيطة
- ركز على الإجابة المفيدة
- تجنب التكرار
- كن دقيقاً
- اعترف إذا لم تعرف"""
    else:
        system_prompt = """You are a smart, professional AI assistant similar to ChatGPT.

## Your personality:
- Intelligent and professional
- Clear and direct
- Friendly but professional
- Concise and effective

## Response rules:
- Be very brief (1-3 sentences for simple questions)
- For complex topics: use bullet points
- Rarely use emojis
- Use natural language
- Focus on useful answers
- Avoid repetition
- Be accurate"""
    
    prompt = f"""{system_prompt}

{context}

## السؤال الحالي:
{user_msg}

## ردك (واضح ومختصر):"""

    # Try with multiple keys
    for attempt in range(len(GEMINI_KEYS) * 2):
        try:
            current_key = key_manager.get_key()
            
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel(
                "gemini-2.0-flash-exp",
                safety_settings=SAFETY,
                generation_config=GEN_CONFIG
            )
            
            response = model.generate_content(prompt)
            
            if not response or not response.text:
                raise ValueError("Empty response")
            
            reply = clean_text(response.text.strip())
            
            if len(reply) < 5:
                raise ValueError("Response too short")
            
            if len(reply) > 1500:
                sentences = reply.split('.')
                reply = '.'.join(sentences[:5]) + '.'
            
            tokens = estimate_tokens(reply)
            key_manager.mark_success(current_key)
            log_event('response_generated', user_id, {'tokens': tokens})
            
            return reply
            
        except Exception as e:
            error = str(e).lower()
            logger.error(f"Error attempt {attempt + 1}: {e}")
            
            is_quota = "quota" in error or "resource" in error
            key_manager.mark_fail(current_key, is_quota)
            
            if "safety" in error or "block" in error:
                log_event('safety_block', user_id)
                return "عذراً، لا أستطيع الرد على هذا الموضوع. دعنا نتحدث عن شيء آخر."
            
            if attempt < len(GEMINI_KEYS) - 1:
                time.sleep(0.5)
                continue
    
    log_event('generation_failed', user_id)
    return "عذراً، حدث خطأ تقني. حاول مرة أخرى بعد قليل."

# LINE Handlers
def send_loading_animation(user_id):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.show_loading_animation(
                ShowLoadingAnimationRequest(
                    chatId=user_id,
                    loadingSeconds=5
                )
            )
    except Exception as e:
        logger.debug(f"Could not send loading animation: {e}")

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    
    welcome_msg = get_welcome_message()
    
    save_user(user_id)
    log_event('user_follow', user_id)
    
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=welcome_msg)]
                )
            )
    except Exception as e:
        logger.error(f"Failed to send welcome: {e}")

@handler.add(UnfollowEvent)
def handle_unfollow(event):
    user_id = event.source.user_id
    log_event('user_unfollow', user_id)

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()
    
    if not user_msg:
        return
    
    if len(user_msg) > 3000:
        reply = f"""الرسالة طويلة جداً

الحد الأقصى: 3000 حرف
رسالتك: {len(user_msg)} حرف

اختصر رسالتك وأعد المحاولة."""
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply)]
                    )
                )
        except:
            pass
        return
    
    # Rate limiting
    if not rate_limiter.is_allowed(user_id, RATE_LIMIT_SECONDS):
        logger.info(f"Rate limit hit for user {user_id}")
        return
    
    # Check daily limit
    if not check_daily_limit(user_id):
        reply = f"""وصلت للحد اليومي

الحد الأقصى: {MAX_DAILY_MESSAGES} رسالة/يوم
يمكنك المتابعة غداً

{BOT_YEAR} - {BOT_CREATOR}"""
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply)]
                    )
                )
        except:
            pass
        return
    
    save_user(user_id)
    send_loading_animation(user_id)
    save_chat(user_id, 'user', user_msg)
    log_event('message_received', user_id)
    
    bot_reply = generate_response(user_msg, user_id)
    save_chat(user_id, 'assistant', bot_reply)
    
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=bot_reply)]
                )
            )
        log_event('message_sent', user_id)
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        log_event('send_failed', user_id, {'error': str(e)})

# Admin Routes
def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.headers.get('X-Admin-Key')
        if not ADMIN_USER_ID or auth != ADMIN_USER_ID:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.route("/admin/stats")
@require_admin
def admin_stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) as total FROM users")
    total_users = c.fetchone()['total']
    
    c.execute("SELECT COUNT(*) as active FROM users WHERE last_seen > ?", 
              [(datetime.now() - timedelta(days=1)).isoformat()])
    active_24h = c.fetchone()['active']
    
    c.execute("SELECT COUNT(*) as total FROM chats")
    total_messages = c.fetchone()['total']
    
    c.execute("SELECT COUNT(*) as today FROM chats WHERE timestamp > ?",
              [datetime.now().date().isoformat()])
    messages_today = c.fetchone()['today']
    
    conn.close()
    
    return jsonify({
        "users": {"total": total_users, "active_24h": active_24h},
        "messages": {"total": total_messages, "today": messages_today},
        "api_keys": key_manager.get_stats(),
        "creator": BOT_CREATOR,
        "year": BOT_YEAR
    })

@app.route("/admin/clean")
@require_admin
def admin_clean():
    clean_old_data()
    return jsonify({"status": "cleaned"})

# Public Routes
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        abort(400)
    
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Error in callback: {e}")
    
    return "OK"

@app.route("/")
def home():
    return jsonify({
        "name": BOT_NAME,
        "version": BOT_VERSION,
        "creator": BOT_CREATOR,
        "year": BOT_YEAR,
        "status": "running",
        "features": [
            "Multi-language support",
            "Smart context memory",
            "Rate limiting",
            "Daily limits",
            "Analytics tracking",
            f"{len(GEMINI_KEYS)} API keys",
            "Loading animations",
            "LINE v3 SDK"
        ]
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "keys": len(GEMINI_KEYS),
        "creator": BOT_CREATOR
    })

@app.route("/stats")
def stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM chats")
    total_messages = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM users WHERE last_seen > ?",
              [(datetime.now() - timedelta(hours=24)).isoformat()])
    active_users = c.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        "total_users": total_users,
        "total_messages": total_messages,
        "active_24h": active_users,
        "api_keys_active": len(GEMINI_KEYS),
        "creator": BOT_CREATOR,
        "year": BOT_YEAR
    })

# Error Handlers
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(403)
def forbidden(e):
    return jsonify({"error": "Forbidden"}), 403

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}")
    return jsonify({"error": "Server error"}), 500

# Background Tasks
def background_cleanup():
    while True:
        try:
            time.sleep(3600)
            clean_old_data()
            logger.info("Background cleanup completed")
        except Exception as e:
            logger.error(f"Background cleanup error: {e}")

# Main
if __name__ == "__main__":
    # Initialize database FIRST before anything else
    logger.info("Initializing database...")
    init_db()
    logger.info("Database ready")
    
    # Start background cleanup thread
    cleanup_thread = threading.Thread(target=background_cleanup, daemon=True)
    cleanup_thread.start()
    
    port = int(os.getenv("PORT", 10000))
    debug = os.getenv("DEBUG", "False").lower() == "true"
    
    logger.info("=" * 60)
    logger.info(f"{BOT_NAME} v{BOT_VERSION}")
    logger.info(f"Created by: {BOT_CREATOR}")
    logger.info(f"Year: {BOT_YEAR}")
    logger.info(f"Port: {port}")
    logger.info(f"API Keys: {len(GEMINI_KEYS)}")
    logger.info(f"Rate Limit: {RATE_LIMIT_SECONDS}s")
    logger.info(f"Daily Limit: {MAX_DAILY_MESSAGES} msgs")
    logger.info("=" * 60)
    
    app.run(host="0.0.0.0", port=port, debug=debug)
