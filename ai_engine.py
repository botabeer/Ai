"""
🤖 AI Response Engine
====================
Groq-powered conversational AI with error handling and optimization
"""

from groq import Groq, GroqError
import logging
from typing import List, Dict, Optional
from datetime import datetime
import random
import time

logger = logging.getLogger(__name__)


class AIEngine:
    """
    Advanced AI response generation engine
    Features:
    - Retry logic with exponential backoff
    - Error handling and recovery
    - Response caching (optional)
    - Performance metrics
    """
    
    # System prompts for different scenarios
    SYSTEM_PROMPTS = {
        'default': """أنت نور، مدربة حياة محترفة ومتعاطفة.

🎯 مهمتك:
- الاستماع بتعاطف وفهم عميق
- تقديم نصائح عملية وواقعية
- تشجيع النمو الشخصي والتفكير الإيجابي
- مساعدة الناس على إيجاد حلول لتحدياتهم

💫 أسلوبك:
- استخدمي لغة عربية بسيطة وودية
- ردودك قصيرة (2-4 جمل) ومباشرة
- استخدمي الإيموجي بشكل مناسب (1-2 فقط)
- كوني إيجابية لكن واقعية
- اطرحي أسئلة تساعد على التأمل

⚠️ مهم:
- لا تعطي نصائح طبية أو قانونية
- إذا كان الموضوع خطير، انصحي بالتواصل مع مختص
- احترمي خصوصية المستخدم""",
        
        'first_time': """أنت نور، مدربة حياة ترحب بمستخدم جديد.

اجعلي الترحيب دافئاً ومشجعاً، واشرحي أنك هنا للاستماع والمساعدة.
اجعلي الرد قصيراً (2-3 جمل) ومرحباً."""
    }
    
    # Error messages in Arabic
    ERROR_MESSAGES = [
        "عذراً، واجهت مشكلة صغيرة 😔\nجربي مرة أخرى بعد قليل 💭",
        "آسفة، لا أستطيع الرد الآن 🙏\nلكن أنا هنا عندما تحتاجيني ✨",
        "حدث خطأ مؤقت 😊\nحاولي مرة أخرى بعد لحظات 🌟",
        "أعتذر عن الإزعاج 💙\nسأكون جاهزة بعد قليل ⏰"
    ]
    
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile",
                 temperature: float = 0.8, max_tokens: int = 200,
                 max_retries: int = 3):
        """
        Initialize AI Engine
        
        Args:
            api_key: Groq API key
            model: Model name
            temperature: Creativity level (0.0-1.0)
            max_tokens: Maximum response length
            max_retries: Maximum retry attempts
        """
        self.client = Groq(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        
        # Performance tracking
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_response_time = 0.0
        
        logger.info(f"🤖 AI Engine initialized: model={model}, "
                   f"temp={temperature}, max_tokens={max_tokens}")
    
    def generate_response(self, user_id: str, message: str,
                         conversation_history: List[Dict[str, str]] = None,
                         is_first_time: bool = False) -> str:
        """
        Generate AI response with retry logic
        
        Args:
            user_id: User identifier
            message: User's message
            conversation_history: Previous messages
            is_first_time: Is this user's first message?
            
        Returns:
            AI-generated response
        """
        start_time = time.time()
        self.total_requests += 1
        
        try:
            # Build messages
            messages = self._build_messages(
                message, 
                conversation_history,
                is_first_time
            )
            
            # Generate with retry
            response = self._generate_with_retry(messages)
            
            # Track success
            response_time = time.time() - start_time
            self.successful_requests += 1
            self.total_response_time += response_time
            
            logger.info(f"✅ Generated response for {user_id[:8]}... "
                       f"in {response_time:.2f}s")
            
            return response
            
        except Exception as e:
            self.failed_requests += 1
            logger.error(f"❌ Failed to generate response: {str(e)}")
            return self._get_error_message()
    
    def _build_messages(self, message: str, 
                       conversation_history: Optional[List[Dict[str, str]]],
                       is_first_time: bool) -> List[Dict[str, str]]:
        """Build message array for API"""
        messages = []
        
        # System prompt
        prompt_key = 'first_time' if is_first_time else 'default'
        messages.append({
            'role': 'system',
            'content': self.SYSTEM_PROMPTS[prompt_key]
        })
        
        # Conversation history
        if conversation_history:
            messages.extend(conversation_history)
        
        # Current message
        messages.append({
            'role': 'user',
            'content': message
        })
        
        return messages
    
    def _generate_with_retry(self, messages: List[Dict[str, str]]) -> str:
        """Generate response with exponential backoff retry"""
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"🔄 Attempt {attempt + 1}/{self.max_retries}")
                
                response = self.client.chat.completions.create(
                    messages=messages,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    top_p=0.9,
                    stream=False
                )
                
                reply = response.choices[0].message.content.strip()
                
                if not reply:
                    raise ValueError("Empty response from API")
                
                return reply
                
            except GroqError as e:
                last_error = e
                logger.warning(f"⚠️ Groq API error (attempt {attempt + 1}): {str(e)}")
                
                # Check if we should retry
                if attempt < self.max_retries - 1:
                    # Exponential backoff
                    wait_time = (2 ** attempt) * 0.5
                    logger.info(f"⏳ Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                else:
                    raise
            
            except Exception as e:
                last_error = e
                logger.error(f"❌ Unexpected error: {str(e)}")
                raise
        
        # If all retries failed
        raise last_error
    
    def _get_error_message(self) -> str:
        """Get random error message"""
        return random.choice(self.ERROR_MESSAGES)
    
    def get_stats(self) -> Dict:
        """Get performance statistics"""
        avg_response_time = (
            self.total_response_time / self.successful_requests 
            if self.successful_requests > 0 else 0
        )
        
        success_rate = (
            self.successful_requests / self.total_requests * 100
            if self.total_requests > 0 else 0
        )
        
        return {
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'failed_requests': self.failed_requests,
            'success_rate': f"{success_rate:.1f}%",
            'average_response_time': f"{avg_response_time:.2f}s"
        }
    
    def reset_stats(self):
        """Reset performance statistics"""
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_response_time = 0.0
        logger.info("📊 Stats reset")
    
    def __repr__(self) -> str:
        stats = self.get_stats()
        return (f"AIEngine(model={self.model}, "
                f"requests={self.total_requests}, "
                f"success_rate={stats['success_rate']})")
