"""
🔧 إصلاح سريع - تغيير النموذج
================================
يختبر النماذج المتوفرة ويختار الأفضل
"""

import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

# النماذج المتوقعة بالترتيب (من الأفضل للأقل)
MODELS_TO_TRY = [
    'gemini-1.5-flash',
    'gemini-1.5-flash-8b',
    'gemini-pro',
    'gemini-1.0-pro'
]

print("🔍 اختبار النماذج المتوفرة...\n")

# استخدم أول مفتاح
api_key = os.getenv('GEMINI_API_KEY_1')
if not api_key:
    print("❌ لا يوجد GEMINI_API_KEY_1 في .env")
    exit(1)

genai.configure(api_key=api_key)

# جرب كل نموذج
working_model = None

for model_name in MODELS_TO_TRY:
    try:
        print(f"⏳ اختبار: {model_name}...", end=" ")
        
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            "قل مرحبا",
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=10,
            )
        )
        
        print(f"✅ يعمل!")
        print(f"   الرد: {response.text}\n")
        
        if not working_model:
            working_model = model_name
            
    except Exception as e:
        print(f"❌ لا يعمل")
        if "404" in str(e):
            print(f"   السبب: النموذج غير موجود\n")
        else:
            print(f"   السبب: {str(e)[:50]}\n")

# النتيجة
print("="*60)
if working_model:
    print(f"✅ النموذج الموصى به: {working_model}")
    print(f"\n📝 عدّل في app.py السطر:")
    print(f"   model = genai.GenerativeModel('{working_model}')")
else:
    print("❌ لا يوجد نموذج يعمل!")
    print("   تحقق من:")
    print("   1. مفتاح API صحيح")
    print("   2. لم يصل للحد اليومي")
    print("   3. اتصال الإنترنت")
print("="*60)
