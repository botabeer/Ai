"""
🤖 Life Coach LINE Bot - Professional Edition
================================================
مدرب حياة ذكي متقدم مع ذاكرة، تحليل مشاعر، وتتبع تقدم

Features:
- نظام ذاكرة ذكي للمحادثات
- تحليل المشاعر والحالة النفسية
- تتبع الأهداف والتقدم
- 3 مفاتيح API مع تبديل تلقائي ذكي
- نظام cache للردود المتكررة
- rate limiting ذكي
- logging احترافي
- معالجة متقدمة للأخطاء
"""

from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, PushMessageRequest
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
import google.generativeai as genai
import os
from datetime import datetime, timedelta
from collections import defaultdict, deque
from functools import wraps
import json
import hashlib
import logging
from typing import Dict, List, Optional, Tuple
import time

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

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ================== مفاتيح Gemini ==================
GEMINI_KEYS = [
    os.getenv('GEMINI_API_KEY_1'),
    os.getenv('GEMINI_API_KEY_2'),
    os.getenv('GEMINI_API_KEY_3')
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
    
    def add_goal(self, user_id: str, goal: str):
        """إضافة هدف للمستخدم"""
        self.goals[user_id].append({
            'goal': goal,
            'created_at': datetime.now().isoformat(),
            'status': 'active',
            'progress': 0
        })
        
    def track_emotion(self, user_id: str, emotion: str, intensity: float):
        """تتبع المشاعر"""
        self.emotions[user_id].append({
            'emotion': emotion,
            'intensity': intensity,
            'timestamp': datetime.now().isoformat()
        })
        # احتفظ بآخر 20 حالة عاطفية فقط
        if len(self.emotions[user_id]) > 20:
            self.emotions[user_id] = self.emotions[user_id][-20:]
    
    def get_emotion_trend(self, user_id: str) -> str:
        """تحليل اتجاه المشاعر"""
        recent = self.emotions[user_id][-5:]
        if not recent:
            return "محايد"
        
        avg_intensity = sum(e['intensity'] for e in recent) / len(recent)
        emotions = [e['emotion'] for e in recent]
        
        if avg_intensity > 0.7:
            return f"إيجابي جداً (غالب: {max(set(emotions), key=emotions.count)})"
        elif avg_intensity > 0.4:
            return "إيجابي"
        elif avg_intensity > -0.2:
            return "محايد"
        else:
            return "يحتاج دعم"
    
    def should_check_in(self, user_id: str) -> bool:
        """هل حان وقت الاطمئنان على المستخدم؟"""
        last = self.last_interaction.get(user_id)
        if not last:
            return False
        return (datetime.now() - last) > timedelta(days=3)

# ================== إدارة المفاتيح الذكية ==================
class SmartKeyManager:
    """إدارة ذكية لمفاتيح API"""
    
    def __init__(self, keys: List[str]):
        self.keys = [k for k in keys if k and 'your_' not in k]
        self.current_index = 0
        self.key_stats = {i: {'calls': 0, 'errors': 0, 'last_reset': datetime.now()} 
                         for i in range(len(self.keys))}
        self.failed_keys = set()
        self.last_reset = datetime.now()
        
    def get_best_key(self) -> Tuple[str, int]:
        """اختيار أفضل مفتاح متاح"""
        # إعادة تعيين يومياً
        if datetime.now() - self.last_reset > timedelta(days=1):
            self.reset_daily()
        
        # جرب المفاتيح بالترتيب من الأقل استخداماً
        available = [(i, self.key_stats[i]['calls']) 
                    for i in range(len(self.keys)) 
                    if i not in self.failed_keys]
        
        if not available:
            raise Exception("جميع المفاتيح مستنفذة")
        
        # اختر المفتاح الأقل استخداماً
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
    
    def __init__(self, ttl: int = 3600):
        self.cache: Dict[str, Tuple[str, datetime]] = {}
        self.ttl = ttl
        
    def _hash_message(self, message: str) -> str:
        """إنشاء hash للرسالة"""
        return hashlib.md5(message.lower().strip().encode()).hexdigest()
    
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
        
        # تنظيف الـ cache القديم
        if len(self.cache) > 100:
            old_keys = [k for k, (_, ts) in self.cache.items() 
                       if datetime.now() - ts > timedelta(seconds=self.ttl)]
            for k in old_keys:
                del self.cache[k]

# ================== Rate Limiting ==================
class RateLimiter:
    """حماية من التكرار الزائد"""
    
    def __init__(self, max_requests: int = 20, window: int = 60):
        self.requests: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_requests))
        self.max_requests = max_requests
        self.window = window
        
    def is_allowed(self, user_id: str) -> bool:
        """التحقق من السماح بالطلب"""
        now = time.time()
        user_requests = self.requests[user_id]
        
        # إزالة الطلبات القديمة
        while user_requests and now - user_requests[0] > self.window:
            user_requests.popleft()
        
        if len(user_requests) >= self.max_requests:
            return False
        
        user_requests.append(now)
        return True

# ================== تحليل المشاعر ==================
class EmotionAnalyzer:
    """تحليل بسيط للمشاعر من النص"""
    
    POSITIVE_KEYWORDS = {
        'سعيد', 'فرح', 'ممتاز', 'رائع', 'جميل', 'محظوظ', 'متحمس', 
        'متفائل', 'راضي', 'ممتن', 'فخور', 'نجحت', 'حققت', 'أحب'
    }
    
    NEGATIVE_KEYWORDS = {
        'حزين', 'تعب', 'ملل', 'زهق', 'قلق', 'خائف', 'متوتر', 'مكتئب',
        'يائس', 'محبط', 'فاشل', 'صعب', 'مشكلة', 'أكره', 'ضايق'
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
cache = ResponseCache(ttl=1800)  # 30 دقيقة
rate_limiter = RateLimiter(max_requests=30, window=60)
emotion_analyzer = EmotionAnalyzer()

# ================== AI Engine ==================
def get_ai_response(user_id: str, message: str) -> str:
    """المحرك الذكي للردود"""
    
    # فحص Rate Limiting
    if not rate_limiter.is_allowed(user_id):
        return "رسائلك سريعة جداً 😊 خذ نفس وارجع بعد دقيقة"
    
    # فحص الـ Cache
    cached = cache.get(message)
    if cached:
        return cached
    
    # تحليل المشاعر
    emotion, intensity = emotion_analyzer.analyze(message)
    memory.track_emotion(user_id, emotion, intensity)
    
    # بناء السياق
    history = memory.get_conversation_history(user_id, limit=3)
    emotion_trend = memory.get_emotion_trend(user_id)
    
    # System Prompt متقدم
    system_prompt = f"""أنت "نور" - مدربة حياة شخصية ذكية وداعمة جداً.

📊 معلومات عن المستخدم:
- الحالة العاطفية الحالية: {emotion} ({intensity:.1f})
- الاتجاه العام: {emotion_trend}
- آخر محادثة: {memory.last_interaction.get(user_id, 'أول مرة')}

💬 محادثات سابقة:
{history}

🎯 شخصيتك:
- صديقة مقربة، دافئة ومتفهمة
- تجمعين بين الحكمة والتحفيز
- ردودك 2-4 جمل، مباشرة وقوية
- لا تستخدمين إيموجي أبداً
- تسألين أسئلة عميقة عندما يحتاج الموقف
- تتذكرين السياق والمحادثات السابقة

🧠 نهجك:
1. إذا كان حزيناً: استمعي بعمق وقدمي دعماً حقيقياً
2. إذا كان متحمساً: شاركيه الفرح وادفعيه للأمام
3. إذا كان محايداً: كوني داعمة وإيجابية
4. دائماً: كوني صادقة، محفزة، وعملية

⚠️ مهم:
- لا تكرري نفس العبارات
- تجنبي الكليشيهات
- كوني أصيلة وإنسانية
- الرد بالعربية فقط"""

    # المحاولة مع التبديل التلقائي
    max_retries = len(key_manager.keys)
    
    for attempt in range(max_retries):
        try:
            key, key_index = key_manager.get_best_key()
            genai.configure(api_key=key)
            
            model = genai.GenerativeModel(
                'gemini-1.5-flash-002',
                generation_config=genai.types.GenerationConfig(
                    temperature=0.95,
                    top_p=0.95,
                    top_k=50,
                    max_output_tokens=250,
                )
            )
            
            response = model.generate_content(
                f"{system_prompt}\n\nالرسالة الحالية: {message}\n\nردك:"
            )
            
            reply = response.text.strip()
            
            # حفظ في الذاكرة
            memory.add_message(user_id, 'user', message, emotion)
            memory.add_message(user_id, 'assistant', reply)
            
            # حفظ في الـ Cache
            cache.set(message, reply)
            
            # تسجيل النجاح
            key_manager.mark_success(key_index)
            logger.info(f"✅ رد ناجح | مفتاح: {key_index+1} | مشاعر: {emotion}")
            
            return reply
            
        except Exception as e:
            error_msg = str(e).lower()
            is_quota = any(word in error_msg for word in ['quota', 'limit', 'resource'])
            
            key_manager.mark_failure(key_index, is_quota)
            logger.error(f"❌ خطأ في محاولة {attempt+1}: {e}")
            
            if attempt < max_retries - 1:
                continue
            else:
                return "عذراً، الخدمة مشغولة حالياً 💭 دعيني أستريح قليلاً وارجعي بعد دقائق"
    
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
        
        logger.info(f"📨 رسالة من {user_id[:8]}...: {message[:50]}")
        
        # الحصول على الرد
        reply = get_ai_response(user_id, message)
        
        # إرسال الرد
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )
        
        logger.info(f"✅ تم الرد بنجاح على {user_id[:8]}...")
        
    except Exception as e:
        logger.error(f"❌ خطأ في handle_message: {e}")
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="عذراً، حصل خطأ مؤقت. جربي مرة ثانية")]
                    )
                )
        except:
            pass

@handler.add(FollowEvent)
def handle_follow(event):
    """معالج متابعة جديدة"""
    user_id = event.source.user_id
    logger.info(f"🎉 متابع جديد: {user_id}")
    
    welcome_message = """مرحباً! أنا نور، مدربة حياتك الشخصية 🌟

أنا هنا لأدعمك، أسمعك، وأساعدك تحققين أهدافك.

شاركيني أي شيء في بالك، وخليني أكون معك في رحلتك."""
    
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=welcome_message)]
                )
            )
    except Exception as e:
        logger.error(f"خطأ في رسالة الترحيب: {e}")

# ================== نقاط النهاية ==================
@app.route("/", methods=['GET'])
def home():
    """الصفحة الرئيسية"""
    stats = {
        'status': 'running',
        'active_users': len(memory.conversations),
        'total_messages': sum(len(conv) for conv in memory.conversations.values()),
        'cache_size': len(cache.cache),
        'available_keys': len(key_manager.keys) - len(key_manager.failed_keys)
    }
    return jsonify(stats)

@app.route("/health", methods=['GET'])
def health():
    """فحص الصحة"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

@app.route("/stats", methods=['GET'])
def stats():
    """إحصائيات متقدمة"""
    return jsonify({
        'users': len(memory.conversations),
        'messages': sum(len(conv) for conv in memory.conversations.values()),
        'goals_tracked': sum(len(goals) for goals in memory.goals.values()),
        'cache_hit_rate': f"{len(cache.cache)}/100",
        'key_usage': {f"key_{i+1}": stats['calls'] 
                     for i, stats in key_manager.key_stats.items()},
        'failed_keys': len(key_manager.failed_keys)
    })

# ================== التشغيل ==================
if __name__ == "__main__":
    logger.info("🚀 بدء تشغيل Life Coach Bot Pro")
    logger.info(f"📊 مفاتيح API متاحة: {len(key_manager.keys)}")
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
