"""
🧪 سكربت اختبار Life Coach Bot
================================
يختبر المفاتيح والاتصال قبل النشر
"""

import os
import sys
from dotenv import load_dotenv
import google.generativeai as genai

# تحميل المتغيرات
load_dotenv()

# الألوان للطباعة
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    """طباعة عنوان"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text:^60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}\n")

def print_success(text):
    """طباعة نجاح"""
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text):
    """طباعة خطأ"""
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text):
    """طباعة تحذير"""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")

def print_info(text):
    """طباعة معلومة"""
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.END}")

# ================== اختبار LINE Configuration ==================
def test_line_config():
    """اختبار إعدادات LINE"""
    print_header("اختبار إعدادات LINE Bot")
    
    token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
    secret = os.getenv('LINE_CHANNEL_SECRET')
    
    if not token or token.startswith('your_'):
        print_error("LINE_CHANNEL_ACCESS_TOKEN غير موجود أو غير صحيح")
        return False
    else:
        print_success(f"LINE_CHANNEL_ACCESS_TOKEN موجود ({len(token)} حرف)")
    
    if not secret or secret.startswith('your_'):
        print_error("LINE_CHANNEL_SECRET غير موجود أو غير صحيح")
        return False
    else:
        print_success(f"LINE_CHANNEL_SECRET موجود ({len(secret)} حرف)")
    
    return True

# ================== اختبار Gemini Keys ==================
def test_gemini_keys():
    """اختبار مفاتيح Gemini"""
    print_header("اختبار مفاتيح Google Gemini API")
    
    keys = [
        os.getenv('GEMINI_API_KEY_1'),
        os.getenv('GEMINI_API_KEY_2'),
        os.getenv('GEMINI_API_KEY_3')
    ]
    
    working_keys = []
    failed_keys = []
    quota_exceeded = []
    
    for i, key in enumerate(keys, 1):
        if not key or key.startswith('your_'):
            print_warning(f"المفتاح {i}: غير موجود أو فارغ")
            continue
        
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            response = model.generate_content(
                "قل مرحبا",
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=10,
                )
            )
            
            print_success(f"المفتاح {i}: يعمل بشكل ممتاز ✨")
            print_info(f"  └─ الرد: {response.text[:30]}...")
            working_keys.append(i)
            
        except Exception as e:
            error_msg = str(e).lower()
            if "quota" in error_msg or "limit" in error_msg or "resource" in error_msg:
                print_warning(f"المفتاح {i}: وصل للحد اليومي")
                quota_exceeded.append(i)
            elif "invalid" in error_msg or "api" in error_msg:
                print_error(f"المفتاح {i}: غير صالح")
                failed_keys.append(i)
            else:
                print_error(f"المفتاح {i}: خطأ - {str(e)[:50]}")
                failed_keys.append(i)
    
    # الملخص
    print(f"\n{Colors.BOLD}{'─'*60}{Colors.END}")
    print(f"{Colors.BOLD}الملخص:{Colors.END}")
    print(f"  {Colors.GREEN}• مفاتيح تعمل: {len(working_keys)}/{len(keys)}{Colors.END}")
    print(f"  {Colors.YELLOW}• وصلت للحد: {len(quota_exceeded)}/{len(keys)}{Colors.END}")
    print(f"  {Colors.RED}• مفاتيح فاشلة: {len(failed_keys)}/{len(keys)}{Colors.END}")
    
    if len(working_keys) == 0 and len(quota_exceeded) == 0:
        print_error("\n⚠️  تحذير: لا يوجد مفاتيح صالحة!")
        print_info("تأكد من:")
        print("    1. المفاتيح صحيحة في ملف .env")
        print("    2. لم تصل لحدها اليومي")
        print("    3. مفعلة في Google AI Studio")
        return False
    elif len(working_keys) > 0:
        print_success(f"\n✅ البوت جاهز للعمل بـ {len(working_keys)} مفتاح")
        return True
    else:
        print_warning("\n⚠️  جميع المفاتيح وصلت للحد اليومي")
        print_info("البوت سيعمل غداً بعد إعادة التعيين التلقائية")
        return True

# ================== اختبار Models المتاحة ==================
def test_available_models():
    """اختبار النماذج المتاحة"""
    print_header("النماذج المتاحة")
    
    key = os.getenv('GEMINI_API_KEY_1')
    if not key or key.startswith('your_'):
        print_error("لا يوجد مفتاح API للاختبار")
        return False
    
    try:
        genai.configure(api_key=key)
        models = genai.list_models()
        
        recommended = [
            'gemini-1.5-flash',
            'gemini-1.5-flash-8b',
            'gemini-1.5-pro',
            'gemini-pro'
        ]
        
        print_info("النماذج الموصى بها:")
        for model in models:
            if 'generateContent' in model.supported_generation_methods:
                model_name = model.name.replace('models/', '')
                if any(rec in model_name for rec in ['flash', 'pro']):
                    if model_name in recommended:
                        print_success(f"  • {model_name} ⭐")
                    else:
                        print(f"  • {model_name}")
        
        return True
        
    except Exception as e:
        print_error(f"خطأ في جلب النماذج: {e}")
        return False

# ================== اختبار الاتصال بالإنترنت ==================
def test_internet():
    """اختبار الاتصال بالإنترنت"""
    print_header("اختبار الاتصال")
    
    try:
        import socket
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        print_success("الاتصال بالإنترنت يعمل")
        return True
    except OSError:
        print_error("لا يوجد اتصال بالإنترنت")
        return False

# ================== الفحص الكامل ==================
def run_all_tests():
    """تشغيل جميع الاختبارات"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}")
    print("┌─────────────────────────────────────────────────────────┐")
    print("│       🧪 Life Coach Bot - Comprehensive Test           │")
    print("└─────────────────────────────────────────────────────────┘")
    print(Colors.END)
    
    results = []
    
    # الاختبارات
    results.append(("الاتصال بالإنترنت", test_internet()))
    results.append(("إعدادات LINE", test_line_config()))
    results.append(("مفاتيح Gemini", test_gemini_keys()))
    results.append(("النماذج المتاحة", test_available_models()))
    
    # النتيجة النهائية
    print_header("النتيجة النهائية")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        if result:
            print_success(f"{name}")
        else:
            print_error(f"{name}")
    
    print(f"\n{Colors.BOLD}{'─'*60}{Colors.END}")
    
    if passed == total:
        print(f"{Colors.GREEN}{Colors.BOLD}")
        print("┌─────────────────────────────────────────────────────────┐")
        print("│              🎉 جميع الاختبارات نجحت!                 │")
        print("│                 البوت جاهز للنشر 🚀                    │")
        print("└─────────────────────────────────────────────────────────┘")
        print(Colors.END)
        return 0
    else:
        print(f"{Colors.RED}{Colors.BOLD}")
        print("┌─────────────────────────────────────────────────────────┐")
        print("│          ⚠️  بعض الاختبارات فشلت                      │")
        print("│          راجع الأخطاء أعلاه وصححها                    │")
        print("└─────────────────────────────────────────────────────────┘")
        print(Colors.END)
        print_info(f"\nنجح {passed}/{total} من الاختبارات")
        return 1

# ================== التشغيل ==================
if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)
