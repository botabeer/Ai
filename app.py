import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot.v3.webhook import WebhookHandler, MessageEvent
from linebot.v3.messaging import MessagingApi, TextMessage as V3TextMessage
import google.generativeai as genai
import random

# ===== إعدادات البوت =====
app = Flask(__name__)

LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')
line_bot_api = MessagingApi()
handler = WebhookHandler(LINE_CHANNEL_SECRET)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY')
if GEMINI_API_KEY and GEMINI_API_KEY != 'YOUR_GEMINI_API_KEY':
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("⚠️ GEMINI_API_KEY not set!")

generation_config = {"temperature":0.85,"top_p":0.95,"top_k":50,"max_output_tokens":1200}
safety_settings = [{"category":"HARM_CATEGORY_HARASSMENT","threshold":"BLOCK_NONE"},
                   {"category":"HARM_CATEGORY_HATE_SPEECH","threshold":"BLOCK_NONE"},
                   {"category":"HARM_CATEGORY_SEXUALLY_EXPLICIT","threshold":"BLOCK_NONE"},
                   {"category":"HARM_CATEGORY_DANGEROUS_CONTENT","threshold":"BLOCK_NONE"}]

try:
    model = genai.GenerativeModel(model_name="gemini-2.0-flash-exp", generation_config=generation_config, safety_settings=safety_settings)
except:
    model = None

# ===== قاعدة البيانات =====
DB_NAME = 'users.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        nickname TEXT,
        last_interaction TIMESTAMP,
        current_step INTEGER DEFAULT 1,
        total_messages INTEGER DEFAULT 0
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        user_message TEXT,
        bot_response TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit(); conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id=?', (user_id,))
    user = cursor.fetchone(); conn.close(); return user

def create_user(user_id):
    conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
    cursor.execute('INSERT INTO users (user_id,last_interaction) VALUES (?,?)', (user_id, datetime.now()))
    conn.commit(); conn.close()

def update_user(user_id, nickname=None, step=None):
    conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
    updates = ['last_interaction=?']; params=[datetime.now()]
    if nickname: updates.append('nickname=?'); params.append(nickname)
    if step: updates.append('current_step=?'); params.append(step)
    updates.append('total_messages = total_messages + 1')
    params.append(user_id)
    cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id=?", params)
    conn.commit(); conn.close()

def save_conversation(user_id, user_message, bot_response):
    conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
    cursor.execute('INSERT INTO conversations (user_id,user_message,bot_response) VALUES (?,?,?)',
                   (user_id,user_message,bot_response))
    conn.commit(); conn.close()

# ===== Gemini AI =====
def generate_ai_response(prompt):
    if not model: return "آسفة، حصل خطأ في AI"
    try:
        chat = model.start_chat(history=[])
        response = chat.send_message(prompt)
        return response.text.strip() if response and response.text else "آسفة، حصل خطأ"
    except:
        return "آسفة، حصل خطأ في AI"

# ===== معالجة الرسائل =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@handler.add(MessageEvent)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    user = get_user(user_id)
    if not user: create_user(user_id); user=get_user(user_id)
    _, nickname, _, step, _ = user

    # خطوة طلب الاسم أو تغيير الاسم
    if step==1 or (step>1 and msg.lower().startswith('تغيير الاسم')):
        reply = "هلا حبيبي، ممكن تقول لي اسمك الجديد؟"
        update_user(user_id, step=2)
    elif step==2:
        name = msg.strip()
        update_user(user_id, nickname=name, step=3)
        reply = f"تم تسجيل اسمك {name}! كيف حالك يا قلبي؟"
    else:
        prompt = f"أنت نور، حبيبة {nickname}. رد على هذا: {msg}"
        reply = generate_ai_response(prompt)
        save_conversation(user_id, msg, reply)
        update_user(user_id)

    line_bot_api.reply_message(event.reply_token, V3TextMessage(text=reply))

# ===== تشغيل البوت =====
if __name__ == "__main__":
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT',5000)), debug=False)
