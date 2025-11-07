import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.v3.messaging import MessagingApi, TextMessage
import google.generativeai as genai
import random

# ===== إعدادات البوت =====
app = Flask(__name__)

# LINE API v3
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')

messaging_api = MessagingApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini AI
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY')
if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_GEMINI_API_KEY':
    print("⚠️ GEMINI_API_KEY not set!")
else:
    genai.configure(api_key=GEMINI_API_KEY)

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
except Exception:
    model = None

# ===== قاعدة البيانات =====
DB_NAME = 'users.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
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

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO users (user_id, last_interaction, current_step) VALUES (?, ?, 1)',
                   (user_id, datetime.now()))
    conn.commit()
    conn.close()

def update_user(user_id, nickname=None, step=None, traits=None, tone=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    updates = ['last_interaction = ?']
    params = [datetime.now()]
    if nickname:
        updates.append('nickname = ?')
        params.append(nickname)
    if step:
        updates.append('current_step = ?')
        params.append(step)
    if traits:
        updates.append('personality_traits = ?')
        params.append(traits)
    if tone:
        updates.append('conversation_tone = ?')
        params.append(tone)
    updates.append('total_messages = total_messages + 1')
    params.append(user_id)
    query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def save_conversation(user_id, user_message, bot_response, emotion=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO conversations (user_id, user_message, bot_response, emotion_detected)
        VALUES (?, ?, ?, ?)
    ''', (user_id, user_message, bot_response, emotion))
    conn.commit()
    conn.close()

def get_conversation_history(user_id, limit=10):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_message, bot_response, timestamp
        FROM conversations
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (user_id, limit))
    history = cursor.fetchall()
    conn.close()
    return list(reversed(history))

def save_memory(user_id, memory_text, memory_type):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO memories (user_id, memory_text, memory_type)
        VALUES (?, ?, ?)
    ''', (user_id, memory_text, memory_type))
    conn.commit()
    conn.close()

def get_memories(user_id, limit=5):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT memory_text, memory_type, created_at
        FROM memories
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    ''', (user_id, limit))
    memories = cursor.fetchall()
    conn.close()
    return memories

# ===== معالجة الرسائل =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add('message')
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    
    # جلب أو إنشاء المستخدم
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    
    user_id, nickname, last_interaction, step, traits, tone, total_messages = user

    # الخطوة 1: طلب الاسم
    if step == 1 or (step >= 2 and not nickname):
        reply = "هلا حبيبي، ممكن اعرف اسمك؟ ودي اناديك فيه"
        update_user(user_id, step=2)
    elif step == 2 and not nickname:
        name = user_message.strip()
        update_user(user_id, nickname=name, step=3)
        reply = f"اهلين {name}! الاسم حلو، كيف حالك يا روحي؟"
    else:
        reply = f"رد تلقائي: {user_message}"  # Placeholder للذكاء الاصطناعي
    
    # إرسال الرد باستخدام MessagingApi v3
    messaging_api.reply_message(
        reply_token=event.reply_token,
        messages=[TextMessage(text=reply)]
    )

# ===== تشغيل البوت =====
if __name__ == "__main__":
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
