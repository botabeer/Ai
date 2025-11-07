import os
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, abort
from linebot.v3.messaging import MessagingApi
from linebot.v3.messaging.models import TextMessage as LineTextMessage
from linebot.v3.webhook import WebhookHandler
from linebot.exceptions import InvalidSignatureError
import google.generativeai as genai
import random

# ===== إعدادات البوت =====
app = Flask(__name__)

# LINE API (v3)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')

messaging_api = MessagingApi(channel_access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini AI
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY')
if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_GEMINI_API_KEY':
    print("⚠️ WARNING: GEMINI_API_KEY not set!")
else:
    print(f"✓ Gemini API Key loaded: {GEMINI_API_KEY[:20]}...")

try:
    genai.configure(api_key=GEMINI_API_KEY)
    print("✓ Gemini API configured successfully")
except Exception as e:
    print(f"❌ Failed to configure Gemini API: {e}")

generation_config = {
    "temperature": 0.85,
    "top_p": 0.95,
    "top_k": 50,
    "max_output_tokens": 1200,
}

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

try:
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash-exp",
        generation_config=generation_config,
        safety_settings=safety_settings
    )
    print("✓ Gemini Model initialized: gemini-2.0-flash-exp")
except Exception as e:
    print(f"❌ Failed to initialize gemini-2.0-flash-exp: {e}")
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        print("✓ Gemini Model initialized: gemini-1.5-flash (fallback)")
    except Exception as e2:
        print(f"❌ Failed to initialize fallback model: {e2}")
        model = None

# ===== قاعدة البيانات =====
DB_NAME = 'users.db'

def get_conn():
    return sqlite3.connect(DB_NAME, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)

def init_db():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            nickname TEXT,
            last_interaction TIMESTAMP,
            current_step INTEGER DEFAULT 1,
            personality_traits TEXT,
            conversation_tone TEXT DEFAULT 'warm',
            total_messages INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_message TEXT,
            bot_response TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            emotion_detected TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            memory_text TEXT,
            memory_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    conn.commit()
    conn.close()

# ===== وظائف DB =====
def get_user(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO users (user_id, last_interaction, current_step) VALUES (?, ?, 1)',
                   (user_id, datetime.now(timezone.utc)))
    conn.commit()
    conn.close()

def update_user(user_id, nickname=None, step=None, traits=None, tone=None):
    conn = get_conn()
    cursor = conn.cursor()
    updates = ['last_interaction = ?']
    params = [datetime.now(timezone.utc)]
    if nickname: updates.append('nickname = ?'); params.append(nickname)
    if step: updates.append('current_step = ?'); params.append(step)
    if traits: updates.append('personality_traits = ?'); params.append(traits)
    if tone: updates.append('conversation_tone = ?'); params.append(tone)
    updates.append('total_messages = total_messages + 1')
    params.append(user_id)
    cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?", params)
    conn.commit()
    conn.close()

def save_conversation(user_id, user_message, bot_response, emotion=None):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO conversations (user_id, user_message, bot_response, emotion_detected) VALUES (?, ?, ?, ?)',
                   (user_id, user_message, bot_response, emotion))
    conn.commit()
    conn.close()

def get_conversation_history(user_id, limit=10):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT user_message, bot_response, timestamp FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?',
                   (user_id, limit))
    history = cursor.fetchall()
    conn.close()
    return list(reversed(history))

def save_memory(user_id, memory_text, memory_type):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO memories (user_id, memory_text, memory_type) VALUES (?, ?, ?)',
                   (user_id, memory_text, memory_type))
    conn.commit()
    conn.close()

def get_memories(user_id, limit=5):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT memory_text, memory_type, created_at FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT ?',
                   (user_id, limit))
    memories = cursor.fetchall()
    conn.close()
    return memories

# ===== وظائف AI =====
def detect_emotion(message):
    emotions = {
        'سعيد': ['فرحان', 'سعيد', 'مبسوط', 'مرتاح', '😊', '😃', '❤️'],
        'حزين': ['حزين', 'زعلان', 'تعبان', '😢', '😭', '💔'],
        'قلق': ['قلقان', 'خايف', 'متوتر'],
        'غاضب': ['عصبي', 'مغاظ', 'غضبان'],
        'متحمس': ['متحمس', 'متشوق', 'ودي', 'ياليت'],
        'ممتن': ['شكرا', 'مشكور', 'يعطيك العافية', 'الله يجزاك', 'ممتن'],
    }
    message_lower = message.lower()
    for emotion, keywords in emotions.items():
        if any(keyword in message_lower for keyword in keywords):
            return emotion
    return 'محايد'

def should_save_memory(message, emotion):
    important_keywords = ['حلم','هدف','أمنية','مشروع','خطة','قرار','عيد ميلاد','ذكرى','تخرج','زواج','عمل','مشكلة','خوف','قلق كبير','سر']
    if emotion in ['حزين', 'قلق', 'غاضب']: return True
    return any(keyword in message for keyword in important_keywords)

def generate_ai_response(prompt):
    try:
        chat = model.start_chat(history=[])
        response = chat.send_message(prompt)
        if not response or not response.text: return None
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini API Error: {type(e).__name__} - {str(e)}")
        return None

# ===== معالجة الرسائل مع تغيير الاسم =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    user_id, nickname, last_interaction, step, traits, tone, total_messages = user

    # أمر تغيير الاسم
    if user_message.lower() == '/change_name':
        msg = f"اسمك الحالي هو \"{nickname}\"، ارسل لي الاسم الجديد" if nickname else "ما سجلت اسمك بعد، ارسل لي اسمك الآن"
        messaging_api.reply_message(event.reply_token, LineTextMessage(text=msg))
        update_user(user_id, step=1)
        return

    # تسجيل الاسم
    if step == 1 or not nickname:
        name = user_message.strip()
        update_user(user_id, nickname=name, step=3)
        reply = f"تم حفظ اسمك: {name}، الحين ممكن نبدأ المحادثة!"
        messaging_api.reply_message(event.reply_token, LineTextMessage(text=reply))
        return

    # محادثة عادية
    history = get_conversation_history(user_id)
    memories = get_memories(user_id)
    prompt = f"أنتِ نور، حبيبة {nickname}. الرسالة: {user_message}"
    reply = generate_ai_response(prompt) or f"آسفة {nickname}، ما فهمت رسالتك، ممكن تعيد؟"
    save_conversation(user_id, user_message, reply, detect_emotion(user_message))
    if should_save_memory(user_message, detect_emotion(user_message)):
        save_memory(user_id, user_message, detect_emotion(user_message))
    update_user(user_id)
    messaging_api.reply_message(event.reply_token, LineTextMessage(text=reply))

# ===== تشغيل البوت =====
if __name__ == "__main__":
    print("🤖 LINE LoveBot v3 Final - Starting...")
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
