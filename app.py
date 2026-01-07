"""
🤖 Life Coach LINE Bot - Professional Edition v2.0
===================================================
مدرب حياة ذكي متقدم مع تحسينات الأداء والاستقرار
"""

from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
import google.generativeai as genai
import os
from datetime import datetime, timedelta
from collections import defaultdict, deque
import hashlib
import logging
from typing import Dict, List, Optional, Tuple
import time
import re

# ================== إعدادات Logging ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ================== التطبيق ==================
app = Flask(__name__)

# ================== إعدادات LINE ==================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    logger.error("❌ LINE credentials missing!")
    raise ValueError("LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET must be set")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ================== مفاتيح Gemini ==================
GEMINI_KEYS = [
    os.getenv('GEMINI_API_KEY_1'),
    os.getenv('GEMINI_API_KEY_2'),
    os.getenv('GEMINI_API_KEY_3')
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k and not k.startswith('your_')]

if not GEMINI_KEYS:
    logger.error("❌ No valid Gemini API keys found!")
    raise ValueError("At least one GEMINI_API_KEY must be set")

# ================== النماذج المتاحة (بالترتيب) ==================
AVAILABLE_MODELS = [
    'gemini-1.5-flash-002',
    'gemini-1.5-flash',
    'gemini-1.5-flash-8b-latest',
    'gemini-pro'
]

# ================== الذاكرة والتخزين ==================
class UserMemory:
    """إدارة ذاكرة المستخدمين بشكل احترافي"""
    
    def __init__(self, max_messages: int = 10):
        self.conversations: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_messages))
        self.user_profiles: Dict[str, dict] = {}
        self.goals: Dict[str, List[dict]] = defaultdict(list)
        self.emotions: Dict[str, List[dict]] = defaultdict(list)
        self.last_interaction: Dict[str, datetime] = {}
        
    def add_message(self, user_id: str, role: str, content: str, emotion: Optional[str] = None):
        """إضافة رسالة للمحادثة"""
        self.conversations[user_id].append({
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat(),
            'emotion': emotion
        })
        self.last_interaction[user_id] = datetime.now()
        
    def get_conversation_history(self, user_id: str, limit: int = 5) -> str:
        """الحصول على تاريخ المحادثة"""
        history = list(self.conversations[user_id])[-limit:]
        if not history:
            return "لا يوجد محادثات سابقة"
        
        formatted = []
        for msg in history:
            role = "المستخدم" if msg['role'] == 'user' else "أنتِ"
            emotion_tag = f" [{msg['emotion']}]" if msg.get('emotion') else ""
            formatted.append(f"{role}{emotion_tag}: {msg['content']}")
        
        return "\n".join(formatted)
    
    def track_emotion(self, user_id: str, emotion: str, intensity: float):
        """تتبع المشاعر"""
        self.emotions[user_id].append({
            'emotion': emotion,
            'intensity': intensity,
            'timestamp': datetime.now().isoformat()
        })
        if len(self.emotions[user_id]) > 20:
            self.emotions[user_id] = self.emotions[user_id][-20:]
    
    def get_emotion_trend(self, user_id: str) -> str:
        """تحليل اتجاه المشاعر"""
        recent = self.emotions[user_id][-5:] if user_id in self.emotions else []
        if not recent:
            return "محايد"
        
        avg_intensity = sum(e['intensity'] for e in recent) / len(recent)
        emotions = [e['emotion'] for e in recent]
        
        if avg_intensity > 0.7:
            return f"إيجابي جداً"
        elif avg_intensity > 0.4:
            return "إيجابي"
        elif avg_intensity > -0.2:
            return "محايد"
        else:
            return "يحتاج دعم"

# ================== إدارة المفاتيح الذكية ==================
class SmartKeyManager:
    """إدارة ذكية لمفاتيح API"""
    
    def __init__(self, keys: List[str]):
        self.keys = keys
        self.current_index = 0
        self.key_stats = {i: {'calls': 0, 'errors': 0, 'last_reset': datetime.now()} 
                         for i in range(len(self.keys))}
        self.failed_keys = set()
        self.last_reset = datetime.now()
        
    def get_best_key(self) -> Tuple[str, int]:
        """اختيار أفضل مفتاح متاح"""
        if datetime.now() - self.last_reset > timedelta(days=1):
            self.reset_daily()
        
        available = [(i, self.key_stats[i]['calls']) 
                    for i in range(len(self.keys)) 
                    if i not in self.failed_keys]
        
        if not available:
            raise Exception("جميع المفاتيح مستنفذة")
        
        best_index = min(available, key=lambda x: x[1])[0]
        return self.keys[best_index], best_index
    
    def mark_success(self, key_index: int):
        """تسجيل نجاح الطلب"""
        self.key_stats[key_index]['calls'] += 1
        
    def mark_failure(self, key_index: int, is_quota: bool = True):
        """تسجيل فشل الطلب"""
        self.key_stats[key_index]['errors'] += 1
        if is_quota:
            self.failed_keys.add(key_index)
            logger.warning(f"المفتاح {key_index + 1} وصل للحد اليومي")
    
    def reset_daily(self):
        """إعادة تعيين يومية"""
        self.failed_keys.clear()
        self.last_reset = datetime.now()
        for stat in self.key_stats.values():
            stat['calls'] = 0
            stat['errors'] = 0
            stat['last_reset'] = datetime.now()
        logger.info("تم إعادة تعيين جميع المفاتيح")

# ================== نظام Cache ==================
class ResponseCache:
    """تخزين مؤقت للردود المتشابهة"""
    
    def __init__(self, ttl: int = 1800):
        self.cache: Dict[str, Tuple[str, datetime]] = {}
        self.ttl = ttl
        
    def _hash_message(self, message: str) -> str:
        """إنشاء hash للرسالة"""
        normalized = re.sub(r'\s+', ' ', message.lower().strip())
        return hashlib.md5(normalized.encode()).hexdigest()
    
    def get(self, message: str) -> Optional[str]:
        """البحث في الـ cache"""
        key = self._hash_message(message)
        if key in self.cache:
            response, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.ttl):
                logger.info("Cache hit! 🎯")
                return response
            else:
                del self.cache[key]
        return None
    
    def set(self, message: str, response: str):
        """حفظ في الـ cache"""
        key = self._hash_message(message)
        self.cache[key] = (response, datetime.now())
        
        if len(self.cache) > 100:
            old_keys = [k for k, (_, ts) in self.cache.items() 
                       if datetime.now() - ts > timedelta(seconds=self.ttl)]
            for k in old_keys:
                del self.cache[k]

# ================== Rate Limiting ==================
class RateLimiter:
    """حماية من التكرار الزائد"""
    
    def __init__(self, max_requests: int = 30, window: int = 60):
        self.requests: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_requests))
        self.max_requests = max_requests
        self.window = window
        
    def is_allowed(self, user_id: str) -> bool:
        """التحقق من السماح بالطلب"""
        now = time.time()
        user_requests = self.requests[user_id]
        
        while user_requests and now - user_requests[0] > self.window:
            user_requests.popleft()
        
        if len(user_requests) >= self.max_requests:
            return False
        
        user_requests.append(now)
        return True

# ================== تحليل المشاعر ==================
class EmotionAnalyzer:
    """تحليل المشاعر من النص"""
    
    POSITIVE_KEYWORDS = {
        'سعيد', 'فرح', 'ممتاز', 'رائع', 'جميل', 'محظوظ', 'متحمس', 
        'متفائل', 'راضي', 'ممتن', 'فخور', 'نجحت', 'حققت', 'أحب',
        'حلو', 'مبسوط', 'مرتاح', 'مسرور', '😊', '😄', '❤️', '🎉'
    }
    
    NEGATIVE_KEYWORDS = {
        'حزين', 'تعب', 'ملل', 'زهق', 'قلق', 'خائف', 'متوتر', 'مكتئب',
        'يائس', 'محبط', 'فاشل', 'صعب', 'مشكلة', 'أكره', 'ضايق', 'زعلان',
        'مضايق', 'مش', 'مو', '😢', '😞', '😔', '💔'
    }
    
    @staticmethod
    def analyze(text: str) -> Tuple[str, float]:
        """تحليل المشاعر: (نوع المشاعر، الشدة من -1 إلى 1)"""
        text_lower = text.lower()
        
        positive_count = sum(1 for word in EmotionAnalyzer.POSITIVE_KEYWORDS if word in text_lower)
        negative_count = sum(1 for word in EmotionAnalyzer.NEGATIVE_KEYWORDS if word in text_lower)
        
        if positive_count > negative_count:
            intensity = min(positive_count / 3, 1.0)
            return "إيجابي", intensity
        elif negative_count > positive_count:
            intensity = -min(negative_count / 3, 1.0)
            return "سلبي", intensity
        else:
            return "محايد", 0.0

# ================== تهيئة الأنظمة ==================
memory = UserMemory()
key_manager = SmartKeyManager(GEMINI_KEYS)
cache = ResponseCache(ttl=1800)
rate_limiter = RateLimiter(max_requests=30, window=60)
emotion_analyzer = EmotionAnalyzer()

# ================== AI Engine ==================
def find_working_model(api_key: str) -> Optional[str]:
    """البحث عن نموذج يعمل"""
    genai.configure(api_key=api_key)
    
    for model_name in AVAILABLE_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                "Hi",
                generation_config=genai.types.GenerationConfig(max_output_tokens=5)
            )
            logger.info(f"✅ النموذج {model_name} يعمل")
            return model_name
        except Exception as e:
            if "404" not in str(e):
                logger.warning(f"⚠️ النموذج {model_name}: {str(e)[:50]}")
            continue
    
    return None

def get_ai_response(user_id: str, message: str) -> str:
    """المحرك الذكي للردود"""
    
    # فحص Rate Limiting
    if not rate_limiter.is_allowed(user_id):
        return "رسائلك سريعة جداً 😊 خذي نفس عميق وارجعي بعد دقيقة"
    
    # معالجة الرسائل الفارغة
    if not message or len(message.strip()) < 2:
        return "يبدو أن رسالتك فارغة. شاركيني أفكارك أو مشاعرك 💭"
    
    # فحص الـ Cache (فقط للرسائل القصيرة المتكررة)
    if len(message) < 50:
        cached = cache.get(message)
        if cached:
            return cached
    
    # تحليل المشاعر
    emotion, intensity = emotion_analyzer.analyze(message)
    memory.track_emotion(user_id, emotion, intensity)
    
    # بناء السياق
    history = memory.get_conversation_history(user_id, limit=3)
    emotion_trend = memory.get_emotion_trend(user_id)
    
    # System Prompt محسّن
    system_prompt = f"""أنت "نور" - مدربة حياة شخصية ذكية وداعمة.

📊 حالة المستخدم:
- المشاعر الحالية: {emotion} ({intensity:.1f})
- الاتجاه العام: {emotion_trend}

💬 آخر 3 رسائل:
{history}

🎯 شخصيتك:
- صديقة مقربة، دافئة ومتفهمة جداً
- تجمعين بين الحكمة والتحفيز
- ردودك 2-4 جمل، مباشرة وقوية
- لا تستخدمين إيموجي إلا نادراً
- تسألين أسئلة عميقة عندما يحتاج الموقف
- تتذكرين السياق دائماً

🧠 نهجك:
1. استمعي بعمق وتفهمي المشاعر
2. قدمي دعماً حقيقياً وعملياً
3. كوني صادقة ومحفزة
4. تجنبي الكليشيهات والتكرار

⚠️ قواعد مهمة:
- الرد بالعربية فقط
- كوني أصيلة وإنسانية
- لا تكرري نفس العبارات
- ركزي على المستخدم لا على نفسك"""

    # المحاولة مع التبديل التلقائي
    max_retries = len(key_manager.keys)
    working_model = None
    
    for attempt in range(max_retries):
        try:
            key, key_index = key_manager.get_best_key()
            
            # إيجاد نموذج يعمل إذا لم نجد بعد
            if not working_model:
                working_model = find_working_model(key)
                if not working_model:
                    raise Exception("لا توجد نماذج متاحة")
            
            genai.configure(api_key=key)
            model = genai.GenerativeModel(
                working_model,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.9,
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=300,
                )
            )
            
            response = model.generate_content(
                f"{system_prompt}\n\nالرسالة: {message}\n\nردك:"
            )
            
            reply = response.text.strip()
            
            # إزالة أي نص غير مرغوب
            reply = re.sub(r'\*\*.*?\*\*', '', reply)  # إزالة bold
            reply = re.sub(r'\n{3,}', '\n\n', reply)  # تقليل الأسطر الفارغة
            
            # حفظ في الذاكرة
            memory.add_message(user_id, 'user', message, emotion)
            memory.add_message(user_id, 'assistant', reply)
            
            # حفظ في الـ Cache (فقط للرسائل القصيرة)
            if len(message) < 50:
                cache.set(message, reply)
            
            # تسجيل النجاح
            key_manager.mark_success(key_index)
            logger.info(f"✅ رد ناجح | مفتاح: {key_index+1} | نموذج: {working_model} | مشاعر: {emotion}")
            
            return reply
            
        except Exception as e:
            error_msg = str(e).lower()
            is_quota = any(word in error_msg for word in ['quota', 'limit', 'resource', 'exhausted'])
            
            key_manager.mark_failure(key_index, is_quota)
            logger.error(f"❌ خطأ في محاولة {attempt+1}/{max_retries}: {str(e)[:100]}")
            
            if "404" in error_msg:
                working_model = None  # جرب نموذج آخر
            
            if attempt < max_retries - 1:
                time.sleep(0.5)  # انتظار قصير قبل المحاولة التالية
                continue
            else:
                return "عذراً، الخدمة مشغولة حالياً 💭 جربي مرة أخرى بعد دقيقة"
    
    return "أعتذر، لا أستطيع الرد الآن. حاولي لاحقاً ❤️"

# ================== معالجات LINE ==================
@app.route("/callback", methods=['POST'])
def callback():
    """معالج Webhook الرئيسي"""
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Callback error: {e}")
        abort(500)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """معالج الرسائل النصية"""
    try:
        user_id = event.source.user_id
        message = event.message.text.strip()
        
        logger.info(f"📨 [{user_id[:8]}]: {message[:50]}")
        
        reply = get_ai_response(user_id, message)
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
        
        logger.info(f"✅ رد مرسل لـ {user_id[:8]}")
        
    except Exception as e:
        logger.error(f"❌ خطأ في handle_message: {e}")
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="عذراً، حصل خطأ. جربي مرة ثانية 🌸")]
                    )
                )
        except:
            pass

@handler.add(FollowEvent)
def handle_follow(event):
    """معالج متابعة جديدة"""
    user_id = event.source.user_id
    logger.info(f"🎉 متابع جديد: {user_id}")
    
    welcome = """مرحباً! أنا نور، مدربتك الشخصية 🌟

أنا هنا لأدعمك في رحلتك.
شاركيني أي شيء في بالك، وخليني أكون معك."""
    
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=welcome)]
                )
            )
    except Exception as e:
        logger.error(f"خطأ في الترحيب: {e}")

# ================== نقاط النهاية ==================
@app.route("/", methods=['GET'])
def home():
    """الصفحة الرئيسية"""
    return jsonify({
        'status': 'running',
        'bot': 'Life Coach Pro v2.0',
        'active_users': len(memory.conversations),
        'total_messages': sum(len(conv) for conv in memory.conversations.values()),
        'available_keys': len(key_manager.keys) - len(key_manager.failed_keys),
        'cache_size': len(cache.cache)
    })

@app.route("/health", methods=['GET'])
def health():
    """فحص الصحة"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

@app.route("/stats", methods=['GET'])
def stats():
    """إحصائيات"""
    return jsonify({
        'users': len(memory.conversations),
        'messages': sum(len(conv) for conv in memory.conversations.values()),
        'cache_hits': len(cache.cache),
        'failed_keys': len(key_manager.failed_keys),
        'key_stats': {f"key_{i+1}": {
            'calls': s['calls'],
            'errors': s['errors']
        } for i, s in key_manager.key_stats.items()}
    })

# ================== التشغيل ==================
if __name__ == "__main__":
    logger.info("🚀 Life Coach Bot Pro v2.0")
    logger.info(f"📊 مفاتيح API: {len(key_manager.keys)}")
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
