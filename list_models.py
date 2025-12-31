import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

# استخدم أول مفتاح متاح
GEMINI_KEY = os.getenv('GEMINI_API_KEY_1')

if not GEMINI_KEY:
    print("❌ لا يوجد مفتاح API في ملف .env")
    exit(1)

print("🔍 جاري جلب النماذج المتاحة...\n")

try:
    genai.configure(api_key=GEMINI_KEY)
    
    models = genai.list_models()
    
    print("✅ النماذج المتاحة للاستخدام:\n")
    print(f"{'اسم النموذج':<40} {'يدعم generateContent'}")
    print("="*70)
    
    available_models = []
    
    for model in models:
        supports_generate = 'generateContent' in model.supported_generation_methods
        if supports_generate:
            status = "✅"
            available_models.append(model.name)
        else:
            status = "❌"
        
        print(f"{model.name:<40} {status}")
    
    print("\n" + "="*70)
    print(f"\n💡 النماذج الموصى بها للبوت:")
    
    recommended = [
        'models/gemini-1.5-flash-latest',
        'models/gemini-1.5-flash',
        'models/gemini-1.5-flash-8b-latest',
        'models/gemini-pro'
    ]
    
    for rec in recommended:
        if rec in available_models:
            print(f"  ✅ {rec}")
    
    print("\n📋 الاستخدام في الكود:")
    if available_models:
        first_model = available_models[0].replace('models/', '')
        print(f"  model = genai.GenerativeModel('{first_model}')")
    
except Exception as e:
    print(f"❌ خطأ: {e}")
    print("\nتأكد من:")
    print("1. المفتاح صحيح في ملف .env")
    print("2. لديك اتصال بالإنترنت")
    print("3. المفتاح مفعل في Google AI Studio")
