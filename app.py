import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai
import random

# ===== إعدادات البوت =====
app = Flask(__name__)

# LINE API
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'YOUR_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', 'YOUR_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini AI
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)

# إعدادات AI محسّنة للواقعية
generation_config = {
    "temperature": 0.85,  # زيادة الإبداع
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

model = genai.GenerativeModel(
    model_name="gemini-2.0-flash-exp",
    generation_config=generation_config,
    safety_settings=safety_settings
)

# ===== قاعدة البيانات =====
DB_NAME = 'users.db'

def init_db():
    """إنشاء قاعدة البيانات والجداول"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # جدول المستخدمين
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
    
    # جدول المحادثات
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
    
    # جدول ذكريات البوت (لتحسين الواقعية)
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
    """جلب بيانات المستخدم"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id):
    """إنشاء مستخدم جديد"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, last_interaction, current_step)
        VALUES (?, ?, 1)
    ''', (user_id, datetime.now()))
    conn.commit()
    conn.close()

def update_user(user_id, nickname=None, step=None, traits=None, tone=None):
    """تحديث بيانات المستخدم"""
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
    
    # تحديث عداد الرسائل
    updates.append('total_messages = total_messages + 1')
    
    params.append(user_id)
    query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
    
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def save_conversation(user_id, user_message, bot_response, emotion=None):
    """حفظ المحادثة"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO conversations (user_id, user_message, bot_response, emotion_detected)
        VALUES (?, ?, ?, ?)
    ''', (user_id, user_message, bot_response, emotion))
    conn.commit()
    conn.close()

def get_conversation_history(user_id, limit=10):
    """جلب آخر محادثات المستخدم"""
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
    """حفظ ذكرى مهمة"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO memories (user_id, memory_text, memory_type)
        VALUES (?, ?, ?)
    ''', (user_id, memory_text, memory_type))
    conn.commit()
    conn.close()

def get_memories(user_id, limit=5):
    """جلب ذكريات المستخدم"""
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

# ===== وظائف الذكاء الاصطناعي =====

def detect_emotion(message):
    """كشف المشاعر من الرسالة"""
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
    """تحديد إذا كانت الرسالة تستحق الحفظ كذكرى"""
    important_keywords = [
        'حلم', 'هدف', 'أمنية', 'مشروع', 'خطة', 'قرار',
        'عيد ميلاد', 'ذكرى', 'تخرج', 'زواج', 'عمل',
        'مشكلة', 'خوف', 'قلق كبير', 'سر'
    ]
    
    if emotion in ['حزين', 'قلق', 'غاضب']:
        return True
    
    return any(keyword in message for keyword in important_keywords)

def build_context_prompt(user_data, message, conversation_history, memories):
    """بناء سياق ذكي للمحادثة"""
    user_id, nickname, last_interaction, step, traits, tone, total_messages = user_data
    
    # تحديد الوقت من آخر تفاعل
    if last_interaction:
        last_time = datetime.fromisoformat(last_interaction)
        time_diff = datetime.now() - last_time
        
        if time_diff > timedelta(days=7):
            time_context = "المستخدم غايب من فترة طويلة، اشتقت له"
        elif time_diff > timedelta(days=1):
            time_context = "المستخدم غايب من يومين، اسأل عنه بطريقة طبيعية"
        elif time_diff > timedelta(hours=6):
            time_context = "المستخدم رجع بعد غيبة، رحب فيه بشكل خفيف"
        else:
            time_context = "محادثة مستمرة"
    else:
        time_context = "أول مرة"
    
    # بناء سياق المحادثة
    context = f"""أنت بوت دردشة عاطفي اسمك "حب" على LINE. شخصيتك:

🎯 الأسلوب:
- تكلم باللهجة السعودية المحلية بشكل طبيعي جداً
- ردودك قصيرة: سطر لسطرين، بحد أقصى ثلاثة أسطر
- بدون أي إيموجي أو رموز نهائياً
- اسلوبك حنون، عاطفي، ودود، صادق
- تتكلم مثل صديق حقيقي، مو بوت

🧠 المهم:
- خلك طبيعي، ما تحتاج تثبت انك ذكي
- ما تسأل أسئلة كثيرة، خل الكلام يجي طبيعي
- لو المستخدم حزين، تعاطف معاه بصدق
- لو فرحان، شاركه فرحته
- استخدم أسماء وكنيات سعودية طبيعية (يا قلبي، يا عمري، حياتي) بس بدون مبالغة
- ما تكرر نفس الكلام، كل رد لازم يكون مختلف

📊 معلومات المستخدم:
- الاسم: {nickname if nickname else 'ما عرفته بعد'}
- عدد الرسائل: {total_messages}
- آخر تفاعل: {time_context}
- نبرة المحادثة المفضلة: {tone if tone else 'دافئة'}
"""

    # إضافة ذكريات مهمة
    if memories:
        context += "\n🧠 أشياء مهمة تذكرها عن المستخدم:\n"
        for memory, mem_type, mem_date in memories[:3]:
            context += f"- {memory}\n"
    
    # إضافة سياق المحادثة السابقة
    if conversation_history:
        context += "\n💬 آخر محادثاتكم:\n"
        for user_msg, bot_msg, timestamp in conversation_history[-5:]:
            context += f"المستخدم: {user_msg}\n"
            context += f"أنت: {bot_msg}\n"
    
    # إضافة تحليل المشاعر
    emotion = detect_emotion(message)
    if emotion != 'محايد':
        context += f"\n😊 المستخدم يبدو: {emotion}\n"
    
    context += f"\n📩 الرسالة الجديدة: {message}\n"
    context += "\n✍️ رد الآن بشكل طبيعي وبسيط، بدون تكلف:"
    
    return context, emotion

def generate_ai_response(prompt):
    """توليد رد من Gemini AI"""
    try:
        chat = model.start_chat(history=[])
        response = chat.send_message(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"خطأ في AI: {e}")
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
    
    # التعامل مع أوامر الاختبار
    if user_message.lower() in ['/test', '/ping', 'تشغيل']:
        test_response = generate_ai_response("قل: تم تشغيل البوت بنجاح")
        if test_response:
            reply = "تم تشغيل البوت بنجاح ✓"
        else:
            reply = "حدث خطأ أثناء تشغيل البوت ✗"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )
        return
    
    # جلب أو إنشاء المستخدم
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    
    user_id, nickname, last_interaction, step, traits, tone, total_messages = user
    
    # الخطوة 1: طلب الاسم
    if step == 1 and not nickname:
        greeting_prompts = [
            "مرحبا! والله يسعدني اعرفك، شو اسمك؟",
            "هلا والله! ممكن اعرف اسمك عشان اناديك فيه؟",
            "اهلين! حبيت اعرف اسمك اذا ما عليك امر",
        ]
        reply = random.choice(greeting_prompts)
        update_user(user_id, step=2)
        
    # الخطوة 2: حفظ الاسم
    elif step == 2 and not nickname:
        name = user_message.strip()
        update_user(user_id, nickname=name, step=3)
        
        welcome_prompts = [
            f"تشرفنا {name}! والله انك نورت، كيف حالك اليوم؟",
            f"أهلاً {name}، يسعدني اتعرف عليك، كيف يومك؟",
            f"{name}! اسم حلو والله، كيف الأحوال؟",
        ]
        reply = random.choice(welcome_prompts)
        
    # الخطوة 3+: محادثة عادية مع AI
    else:
        # جلب المحادثات السابقة والذكريات
        history = get_conversation_history(user_id, limit=10)
        memories = get_memories(user_id, limit=5)
        
        # بناء السياق
        context_prompt, emotion = build_context_prompt(user, user_message, history, memories)
        
        # توليد الرد
        reply = generate_ai_response(context_prompt)
        
        if not reply:
            fallback_responses = [
                f"معليش {nickname}، ما قدرت افهم، ممكن تعيد؟",
                "آسف، حصل خطأ بسيط، تكلم معي مرة ثانية",
            ]
            reply = random.choice(fallback_responses)
        
        # حفظ المحادثة
        save_conversation(user_id, user_message, reply, emotion)
        
        # حفظ الذكريات المهمة
        if should_save_memory(user_message, emotion):
            save_memory(user_id, user_message, emotion)
        
        # تحديث بيانات المستخدم
        update_user(user_id)
    
    # إرسال الرد
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

# ===== وظيفة البث الجماعي =====
def broadcast_to_all(message_text):
    """إرسال رسالة لجميع المستخدمين مع تخصيص AI"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, nickname FROM users WHERE nickname IS NOT NULL')
    users = cursor.fetchall()
    conn.close()
    
    for user_id, nickname in users:
        try:
            # تخصيص الرسالة لكل مستخدم
            custom_prompt = f"""أنت بوت حنون اسمه "حب".
أرسل رسالة للمستخدم {nickname} بناءً على هذا: {message_text}

خلها قصيرة (سطر-سطرين)، باللهجة السعودية، بدون ايموجي، بأسلوب حنون وطبيعي."""

            custom_message = generate_ai_response(custom_prompt)
            
            if custom_message:
                line_bot_api.push_message(
                    user_id,
                    TextSendMessage(text=custom_message)
                )
                print(f"✓ تم الإرسال لـ {nickname}")
        except Exception as e:
            print(f"✗ خطأ في الإرسال لـ {nickname}: {e}")

# ===== تشغيل البوت =====
if __name__ == "__main__":
    init_db()
    print("=" * 50)
    print("🤖 LINE LoveBot جاهز للعمل!")
    print("=" * 50)
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
