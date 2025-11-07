import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.v3.messaging import MessagingApi
import google.generativeai as genai
import random

# ===== إعدادات البوت =====
app = Flask(__name__)

# LINE API
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')

messaging_api = MessagingApi.from_channel_access_token(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini AI
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY')

if GEMINI_API_KEY and GEMINI_API_KEY != 'YOUR_GEMINI_API_KEY':
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
except:
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config=generation_config,
        safety_settings=safety_settings
    )

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
    cursor.execute('INSERT INTO conversations (user_id, user_message, bot_response, emotion_detected) VALUES (?, ?, ?, ?)',
                   (user_id, user_message, bot_response, emotion))
    conn.commit()
    conn.close()

def get_conversation_history(user_id, limit=10):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_message, bot_response, timestamp FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?', (user_id, limit))
    history = cursor.fetchall()
    conn.close()
    return list(reversed(history))

def save_memory(user_id, memory_text, memory_type):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO memories (user_id, memory_text, memory_type) VALUES (?, ?, ?)',
                   (user_id, memory_text, memory_type))
    conn.commit()
    conn.close()

def get_memories(user_id, limit=5):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT memory_text, memory_type, created_at FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT ?', (user_id, limit))
    memories = cursor.fetchall()
    conn.close()
    return memories

# ===== الذكاء الاصطناعي =====
def detect_emotion(message):
    emotions = {
        'سعيد': ['فرحان', 'سعيد', 'مبسوط', 'مرتاح', '😊', '😃', '❤️', 'حلو', 'جميل', 'رائع'],
        'حزين': ['حزين', 'زعلان', 'تعبان', 'مضايق', '😢', '😭', '💔', 'صعب', 'متضايق'],
        'قلق': ['قلقان', 'خايف', 'متوتر', 'خوف', 'قلق', 'مرعوب'],
        'غاضب': ['زعلان', 'عصبي', 'مغاظ', 'متضايق', 'غضبان'],
        'متحمس': ['متحمس', 'متشوق', 'ودي', 'نفسي', 'ياليت'],
        'ممتن': ['شكرا', 'مشكور', 'يعطيك العافية', 'الله يجزاك', 'ممتن'],
    }
    message_lower = message.lower()
    for emotion, keywords in emotions.items():
        if any(keyword in message_lower for keyword in keywords):
            return emotion
    return 'محايد'

def should_save_memory(message, emotion):
    important_keywords = ['حلم', 'هدف', 'أمنية', 'مشروع', 'خطة', 'قرار','عيد ميلاد', 'ذكرى', 'تخرج', 'زواج', 'عمل','مشكلة', 'خوف', 'قلق كبير', 'سر']
    if emotion in ['حزين', 'قلق', 'غاضب']:
        return True
    return any(keyword in message for keyword in important_keywords)

def build_context_prompt(user_data, message, conversation_history, memories):
    user_id, nickname, last_interaction, step, traits, tone, total_messages = user_data
    if last_interaction:
        last_time = datetime.fromisoformat(last_interaction)
        time_diff = datetime.now() - last_time
        if time_diff > timedelta(days=7): time_context = "حبيبك غايب عنك من أسبوع، اشتقتي له"
        elif time_diff > timedelta(days=1): time_context = "حبيبك غايب يومين، اسأليه عنه بحنان"
        elif time_diff > timedelta(hours=6): time_context = "حبيبك رجع بعد غيبة، رحبي فيه بدفء"
        else: time_context = "محادثة مستمرة معاه"
    else: time_context = "أول مرة تكلمينه"

    context = f"أنت نور، حنونة ورومانسية.\n💌 رسالته الجديدة: {message}\nآخر كلام: {time_context}\n"
    if memories: context += "\nذكريات:\n" + "\n".join(mem[0] for mem in memories[:3])
    if conversation_history:
        context += "\nآخر المحادثات:\n"
        for user_msg, bot_msg, _ in conversation_history[-5:]:
            context += f"هو: {user_msg}\nأنت: {bot_msg}\n"
    emotion = detect_emotion(message)
    return context, emotion

def generate_ai_response(prompt):
    try:
        chat = model.start_chat(history=[])
        response = chat.send_message(prompt)
        if not response or not response.text:
            return None
        return response.text.strip()
    except:
        return None

# ===== معالجة الرسائل =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
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

    # أوامر عامة
    if user_message.lower() in ['/start']:
        reply = "هلا حبيبي! تقدر تتواصل معي، أو تغير اسمك بـ 'تغيير الاسم [الاسم الجديد]'."
        messaging_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    if user_message.lower() in ['/help']:
        reply = "أوامر البوت:\n/start - بدء المحادثة\n/help - عرض المساعدة\n/ping - اختبار البوت\nتغيير الاسم [الاسم] - لتغيير اسمك."
        messaging_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    if user_message.lower() in ['/ping', 'تشغيل']:
        reply = "البوت شغال ✓"
        messaging_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # جلب المستخدم
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)

    user_id, nickname, last_interaction, step, traits, tone, total_messages = user

    # تغيير الاسم
    if user_message.lower().startswith('تغيير الاسم'):
        new_name = user_message.replace('تغيير الاسم', '').strip()
        if new_name:
            update_user(user_id, nickname=new_name)
            reply = f"تم تغيير اسمك إلى {new_name} ✓"
        else:
            reply = "اكتب الاسم الجديد بعد 'تغيير الاسم'"
        messaging_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # الخطوة 1: طلب الاسم
    if step == 1 and not nickname:
        greeting_prompts = ["هلا حبيبي، ممكن اعرف اسمك؟", "مرحبا يا قلبي، شو اسمك؟", "هاي! ممكن تقولي اسمك؟"]
        reply = random.choice(greeting_prompts)
        update_user(user_id, step=2)
    # الخطوة 2: حفظ الاسم
    elif step == 2 and not nickname:
        name = user_message.strip()
        update_user(user_id, nickname=name, step=3)
        reply = f"تشرفت {name}! كيف حالك يا قلبي؟"
    # الخطوة 3+: محادثة AI
    else:
        history = get_conversation_history(user_id, limit=10)
        memories = get_memories(user_id, limit=5)
        context_prompt, emotion = build_context_prompt(user, user_message, history, memories)
        reply = generate_ai_response(context_prompt)
        if not reply:
            fallback_responses = [f"معليش {nickname}، ما وصلتني رسالتك زين، ممكن تعيد؟", "آسفة حبيبي، حصل خطأ بسيط، كلمني مرة ثانية"]
            reply = random.choice(fallback_responses)
        save_conversation(user_id, user_message, reply, emotion)
        if should_save_memory(user_message, emotion):
            save_memory(user_id, user_message, emotion)
        update_user(user_id)

    messaging_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# ===== تشغيل البوت =====
if __name__ == "__main__":
    init_db()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
