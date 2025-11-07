import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi
from linebot.v3.messaging import MessagingApi, TextMessage as V3TextMessage
from linebot.v3.webhook import WebhookHandler, WebhookRequest
from linebot.exceptions import LineBotApiError
import google.generativeai as genai
import random

# ===== إعدادات البوت =====
app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini AI
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY')

if not GEMINI_API_KEY or GEMINI_API_KEY == 'YOUR_GEMINI_API_KEY':
    print("⚠️ WARNING: GEMINI_API_KEY not set!")
else:
    genai.configure(api_key=GEMINI_API_KEY)
    print("✓ Gemini API configured successfully")

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
    cursor.execute('INSERT INTO users (user_id, last_interaction, current_step) VALUES (?, ?, 1)', (user_id, datetime.now()))
    conn.commit()
    conn.close()

def update_user(user_id, nickname=None, step=None):
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
    updates.append('total_messages = total_messages + 1')
    params.append(user_id)
    query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def save_conversation(user_id, user_message, bot_response, emotion=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO conversations (user_id, user_message, bot_response, emotion_detected) VALUES (?, ?, ?, ?)',
                   (user_id, user_message, bot_response, emotion))
    conn.commit()
    conn.close()

# ===== معالجة الرسائل =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(WebhookRequest(body, signature))
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        abort(400)
    
    return 'OK'

@handler.add(V3TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    
    user_id, nickname, last_interaction, step, traits, tone, total_messages = user
    
    # مثال تبسيطي للخطوات
    if step == 1 and not nickname:
        reply = "هلا! ممكن اعرف اسمك؟"
        update_user(user_id, step=2)
    elif step == 2 and not nickname:
        name = user_message.strip()
        update_user(user_id, nickname=name, step=3)
        reply = f"اهلين {name}!"
    else:
        reply = f"وصلتك رسالتك: {user_message}"
        save_conversation(user_id, user_message, reply)
        update_user(user_id)
    
    try:
        line_bot_api.reply_message(event.reply_token, V3TextMessage(text=reply))
    except LineBotApiError as e:
        print(f"❌ Reply error: {e}")

# ===== تشغيل البوت =====
if __name__ == "__main__":
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
