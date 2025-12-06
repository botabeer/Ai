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
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from dotenv import load_dotenv
import google.generativeai as genai
import logging
import time
import threading

# ===================== Logging =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

# ===================== Configuration =====================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3")
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise ValueError("Missing LINE credentials")

if not GEMINI_KEYS:
    raise ValueError("Missing Gemini API keys")

# ===================== LINE v3 Setup =====================
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===================== API Key Manager =====================
class SmartKeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.index = 0
        self.stats = {k: {'fails': 0, 'success': 0, 'last_fail': 0} for k in keys}
        self.lock = threading.Lock()
        logger.info(f"✅ Initialized with {len(keys)} API keys")
    
    def get_key(self):
        with self.lock:
            # Try all keys
            for _ in range(len(self.keys)):
                key = self.keys[self.index]
                stats = self.stats[key]
                
                # Reset after 10 minutes
                if stats['last_fail'] and (time.time() - stats['last_fail']) > 600:
                    stats['fails'] = 0
                    stats['last_fail'] = 0
                
                # Skip if too many fails
                if stats['fails'] < 5:
                    self.index = (self.index + 1) % len(self.keys)
                    return key
                
                self.index = (self.index + 1) % len(self.keys)
            
            # All keys failed, reset and return first
            logger.warning("All keys exhausted, resetting")
            for k in self.stats:
                self.stats[k]['fails'] = 0
            return self.keys[0]
    
    def mark_fail(self, key):
        with self.lock:
            if key in self.stats:
                self.stats[key]['fails'] += 1
                self.stats[key]['last_fail'] = time.time()
                logger.warning(f"Key failed. Total fails: {self.stats[key]['fails']}")
    
    def mark_success(self, key):
        with self.lock:
            if key in self.stats:
                self.stats[key]['success'] += 1
                if self.stats[key]['fails'] > 0:
                    self.stats[key]['fails'] = max(0, self.stats[key]['fails'] - 1)

key_manager = SmartKeyManager(GEMINI_KEYS)

# ===================== Gemini Config =====================
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

# ===================== Database =====================
DB_PATH = "chatbot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        name TEXT,
        first_seen TEXT,
        last_seen TEXT,
        msg_count INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        role TEXT,
        content TEXT,
        timestamp TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_user ON chats(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_time ON chats(timestamp)')
    
    conn.commit()
    conn.close()
    logger.info("✅ Database ready")

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
    
    user = get_user(user_id)
    if user:
        c.execute(
            "UPDATE users SET last_seen=?, msg_count=msg_count+1 WHERE user_id=?",
            (now, user_id)
        )
    else:
        c.execute(
            "INSERT INTO users (user_id, name, first_seen, last_seen, msg_count) VALUES (?, ?, ?, ?, 1)",
            (user_id, name, now, now)
        )
    
    conn.commit()
    conn.close()

def save_chat(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO chats (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, role, content, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_history(user_id, limit=6):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Clean old messages (older than 24 hours)
    yesterday = (datetime.now() - timedelta(hours=24)).isoformat()
    c.execute("DELETE FROM chats WHERE user_id=? AND timestamp < ?", (user_id, yesterday))
    
    c.execute(
        "SELECT role, content FROM chats WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.commit()
    conn.close()
    
    return list(reversed(rows))

# ===================== AI Engine =====================
def clean_text(text):
    """Remove emojis"""
    import re
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
    return emoji_pattern.sub('', text).strip()

def generate_response(user_msg, user_id):
    """Generate ChatGPT-style response"""
    
    # Get conversation history
    history = get_history(user_id, limit=6)
    context = ""
    
    if history:
        context = "\n## السياق:\n"
        for msg in history[-4:]:
            role = "المستخدم" if msg['role'] == 'user' else "المساعد"
            context += f"{role}: {msg['content']}\n"
    
    # Build prompt
    prompt = f"""أنت مساعد ذكي يتحدث العربية بطلاقة، أسلوبك مشابه لـ ChatGPT.

## المبادئ:
- كن مختصراً جداً (1-3 جمل فقط)
- لا تستخدم إيموجي إلا للضرورة القصوى
- كن واضحاً ومباشراً
- استخدم لغة طبيعية وبسيطة
- ركز على الإجابة المفيدة فقط
- تجنب التكرار
- كن ودوداً لكن احترافياً
- لا تبالغ في اللطف

## أمثلة على الأسلوب:
سؤال: كيف أتحسن في البرمجة؟
جواب: مارس يومياً وحل مشاكل حقيقية. ابدأ بمشاريع صغيرة وطور مهاراتك تدريجياً.

سؤال: أشعر بالتعب
جواب: خذ راحة كافية ومارس الرياضة. النوم الجيد والأكل الصحي مهمين أيضاً.

{context}

## السؤال الحالي:
{user_msg}

## ردك (1-3 جمل، واضح ومختصر):"""

    # Try with multiple keys
    for attempt in range(len(GEMINI_KEYS)):
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
            
            # Limit length
            if len(reply) > 1000:
                sentences = reply.split('.')
                reply = '.'.join(sentences[:3]) + '.'
            
            key_manager.mark_success(current_key)
            return reply
            
        except Exception as e:
            error = str(e).lower()
            logger.error(f"Error attempt {attempt + 1}: {e}")
            
            key_manager.mark_fail(current_key)
            
            if "quota" in error or "resource" in error:
                if attempt < len(GEMINI_KEYS) - 1:
                    continue
            elif "safety" in error or "block" in error:
                return "عذراً، لا أستطيع الرد على هذا الموضوع."
            else:
                if attempt < len(GEMINI_KEYS) - 1:
                    time.sleep(1)
                    continue
    
    return "عذراً، حدث خطأ. حاول مرة أخرى."

# ===================== LINE Handler =====================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()
    
    # Validate message
    if not user_msg or len(user_msg) > 3000:
        return
    
    # Save user
    save_user(user_id)
    
    # Save user message
    save_chat(user_id, 'user', user_msg)
    
    # Generate response
    bot_reply = generate_response(user_msg, user_id)
    
    # Save bot response
    save_chat(user_id, 'assistant', bot_reply)
    
    # Send reply
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=bot_reply)]
                )
            )
    except Exception as e:
        logger.error(f"Failed to send message: {e}")

# ===================== Routes =====================
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
        logger.error(f"Error: {e}")
    
    return "OK"

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "name": "Smart ChatBot",
        "version": "1.0",
        "keys": len(GEMINI_KEYS)
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })

@app.route("/stats")
def stats():
    """Get bot statistics"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM chats")
    total_messages = c.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        "total_users": total_users,
        "total_messages": total_messages,
        "active_keys": len(GEMINI_KEYS)
    })

# ===================== Error Handlers =====================
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Server error"}), 500

# ===================== Main =====================
if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 10000))
    debug = os.getenv("DEBUG", "False").lower() == "true"
    
    logger.info("=" * 50)
    logger.info("🚀 Smart ChatBot Starting")
    logger.info(f"📌 Port: {port}")
    logger.info(f"🔑 API Keys: {len(GEMINI_KEYS)}")
    logger.info(f"🐛 Debug: {debug}")
    logger.info("=" * 50)
    
    app.run(host="0.0.0.0", port=port, debug=debug)
