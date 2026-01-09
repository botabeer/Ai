"""
🧪 اختبار Life Coach Bot - Groq Version
========================================
يختبر جميع المكونات قبل النشر
"""

import os
import sys
from dotenv import load_dotenv

# ألوان للطباعة
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text:^70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*70}{Colors.END}\n")

def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.END}")

# تحميل المتغيرات
load_dotenv()

# ==================== اختبار 1: Environment Variables ====================
def test_environment():
    print_header("اختبار 1: متغيرات البيئة")
    
    all_good = True
    
    # LINE Bot
    line_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
    line_secret = os.getenv('LINE_CHANNEL_SECRET')
    
    if not line_token or line_token.startswith('your_'):
        print_error("LINE_CHANNEL_ACCESS_TOKEN مفقود أو غير صحيح")
        all_good = False
    else:
        print_success(f"LINE_CHANNEL_ACCESS_TOKEN موجود ({len(line_token)} حرف)")
    
    if not line_secret or line_secret.startswith('your_'):
        print_error("LINE_CHANNEL_SECRET مفقود أو غير صحيح")
        all_good = False
    else:
        print_success(f"LINE_CHANNEL_SECRET موجود ({len(line_secret)} حرف)")
    
    # Groq API
    groq_key = os.getenv('GROQ_API_KEY')
    
    if not groq_key or groq_key.startswith('your_'):
        print_error("GROQ_API_KEY مفقود أو غير صحيح")
        print_info("احصل عليه من: https://console.groq.com/keys")
        all_good = False
    else:
        if groq_key.startswith('gsk_'):
            print_success(f"GROQ_API_KEY موجود وصحيح ({len(groq_key)} حرف)")
        else:
            print_warning("GROQ_API_KEY موجود لكن لا يبدأ بـ 'gsk_' (تحقق من صحته)")
    
    return all_good

# ==================== اختبار 2: Groq Connection ====================
def test_groq_connection():
    print_header("اختبار 2: الاتصال بـ Groq API")
    
    groq_key = os.getenv('GROQ_API_KEY')
    
    if not groq_key or groq_key.startswith('your_'):
        print_error("لا يمكن الاختبار - المفتاح غير موجود")
        return False
    
    try:
        from groq import Groq
        
        print_info("جاري الاتصال بـ Groq...")
        
        client = Groq(api_key=groq_key)
        
        # اختبار بسيط
        response = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "أنت مساعد مفيد."
                },
                {
                    "role": "user",
                    "content": "قل مرحبا"
                }
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.7,
            max_tokens=20
        )
        
        reply = response.choices[0].message.content
        
        print_success("الاتصال بـ Groq يعمل بنجاح!")
        print_info(f"الرد: {reply}")
        
        # معلومات إضافية
        print_info(f"النموذج المستخدم: llama-3.3-70b-versatile")
        print_info(f"الحد اليومي: 1000 طلب مجاناً")
        
        return True
        
    except ImportError:
        print_error("مكتبة groq غير مثبتة!")
        print_info("قم بتثبيتها: pip install groq")
        return False
        
    except Exception as e:
        error_msg = str(e).lower()
        
        if 'api key' in error_msg or 'authentication' in error_msg:
            print_error("المفتاح غير صالح!")
            print_info("تحقق من مفتاح API في https://console.groq.com/keys")
        elif 'rate limit' in error_msg or 'quota' in error_msg:
            print_warning("وصلت للحد اليومي (1000 طلب)")
            print_info("سيعود للعمل غداً تلقائياً")
        else:
            print_error(f"خطأ: {str(e)[:100]}")
        
        return False

# ==================== اختبار 3: LINE SDK ====================
def test_line_sdk():
    print_header("اختبار 3: LINE Bot SDK")
    
    try:
        from linebot.v3 import WebhookHandler
        from linebot.v3.messaging import Configuration, ApiClient, MessagingApi
        
        print_success("مكتبة LINE Bot SDK مثبتة بنجاح")
        
        line_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
        line_secret = os.getenv('LINE_CHANNEL_SECRET')
        
        if line_token and not line_token.startswith('your_'):
            config = Configuration(access_token=line_token)
            print_success("إعدادات LINE Bot جاهزة")
        
        if line_secret and not line_secret.startswith('your_'):
            handler = WebhookHandler(line_secret)
            print_success("معالج Webhook جاهز")
        
        return True
        
    except ImportError as e:
        print_error(f"خطأ في استيراد LINE SDK: {e}")
        print_info("قم بتثبيتها: pip install line-bot-sdk")
        return False
    except Exception as e:
        print_error(f"خطأ: {e}")
        return False

# ==================== اختبار 4: Flask ====================
def test_flask():
    print_header("اختبار 4: Flask Framework")
    
    try:
        from flask import Flask
        
        app = Flask(__name__)
        
        @app.route('/test')
        def test():
            return 'OK'
        
        print_success("Flask مثبت ويعمل بنجاح")
        return True
        
    except ImportError:
        print_error("Flask غير مثبت!")
        print_info("قم بتثبيته: pip install flask")
        return False
    except Exception as e:
        print_error(f"خطأ: {e}")
        return False

# ==================== اختبار 5: الاتصال بالإنترنت ====================
def test_internet():
    print_header("اختبار 5: الاتصال بالإنترنت")
    
    try:
        import socket
        
        # اختبار DNS
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        print_success("الاتصال بالإنترنت يعمل")
        
        # اختبار HTTPS
        import urllib.request
        urllib.request.urlopen('https://www.google.com', timeout=3)
        print_success("اتصال HTTPS يعمل")
        
        return True
        
    except Exception as e:
        print_error(f"لا يوجد اتصال بالإنترنت: {e}")
        return False

# ==================== اختبار شامل للبوت ====================
def test_bot_conversation():
    print_header("اختبار 6: محادثة كاملة مع البوت")
    
    try:
        from groq import Groq
        
        groq_key = os.getenv('GROQ_API_KEY')
        if not groq_key or groq_key.startswith('your_'):
            print_warning("تخطي الاختبار - لا يوجد مفتاح Groq")
            return True
        
        client = Groq(api_key=groq_key)
        
        # محادثة تجريبية
        messages = [
            {"role": "system", "content": "أنت نور، مدربة حياة ودودة."},
            {"role": "user", "content": "مرحبا، أشعر بالتوتر"}
        ]
        
        print_info("إرسال رسالة تجريبية...")
        
        response = client.chat.completions.create(
            messages=messages,
            model="llama-3.3-70b-versatile",
            temperature=0.8,
            max_tokens=150
        )
        
        reply = response.choices[0].message.content
        
        print_success("البوت أجاب بنجاح!")
        print(f"\n{Colors.MAGENTA}المستخدم: مرحبا، أشعر بالتوتر{Colors.END}")
        print(f"{Colors.GREEN}نور: {reply}{Colors.END}\n")
        
        return True
        
    except Exception as e:
        print_error(f"خطأ في المحادثة: {e}")
        return False

# ==================== النتيجة النهائية ====================
def run_all_tests():
    print(f"\n{Colors.BOLD}{Colors.MAGENTA}")
    print("╔═══════════════════════════════════════════════════════════════════╗")
    print("║       🧪 Life Coach Bot - اختبار شامل                           ║")
    print("║       Version 2.0 - Groq Edition                                  ║")
    print("╚═══════════════════════════════════════════════════════════════════╝")
    print(Colors.END)
    
    tests = [
        ("متغيرات البيئة", test_environment),
        ("الاتصال بـ Groq", test_groq_connection),
        ("LINE Bot SDK", test_line_sdk),
        ("Flask Framework", test_flask),
        ("الاتصال بالإنترنت", test_internet),
        ("محادثة تجريبية", test_bot_conversation)
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print_error(f"خطأ في اختبار {name}: {e}")
            results.append((name, False))
    
    # النتيجة
    print_header("النتيجة النهائية")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        if result:
            print_success(f"{name}")
        else:
            print_error(f"{name}")
    
    print(f"\n{Colors.BOLD}{'─'*70}{Colors.END}")
    
    percentage = (passed / total) * 100
    
    if passed == total:
        print(f"{Colors.GREEN}{Colors.BOLD}")
        print("╔═══════════════════════════════════════════════════════════════════╗")
        print("║              ✨ جميع الاختبارات نجحت! ✨                        ║")
        print("║              البوت جاهز للنشر 100% 🚀                            ║")
        print("╚═══════════════════════════════════════════════════════════════════╝")
        print(Colors.END)
        return 0
    elif percentage >= 70:
        print(f"{Colors.YELLOW}{Colors.BOLD}")
        print("╔═══════════════════════════════════════════════════════════════════╗")
        print(f"║      ⚠️  نجح {passed}/{total} من الاختبارات ({percentage:.0f}%)                      ║")
        print("║      البوت يعمل لكن راجع الأخطاء أعلاه                          ║")
        print("╚═══════════════════════════════════════════════════════════════════╝")
        print(Colors.END)
        return 1
    else:
        print(f"{Colors.RED}{Colors.BOLD}")
        print("╔═══════════════════════════════════════════════════════════════════╗")
        print(f"║      ❌ نجح {passed}/{total} فقط ({percentage:.0f}%)                             ║")
        print("║      راجع جميع الأخطاء وصححها قبل النشر                         ║")
        print("╚═══════════════════════════════════════════════════════════════════╝")
        print(Colors.END)
        return 1

if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)
