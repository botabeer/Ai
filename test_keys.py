import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_KEYS = [
    os.getenv('GEMINI_API_KEY_1'),
    os.getenv('GEMINI_API_KEY_2'),
    os.getenv('GEMINI_API_KEY_3')
]

print("🔍 اختبار مفاتيح Gemini API...\n")

working_keys = []
failed_keys = []

for i, key in enumerate(GEMINI_KEYS):
    if not key or key == 'your_first_gemini_api_key_here' or key == 'your_second_gemini_api_key_here' or key == 'your_third_gemini_api_key_here':
        print(f"❌ المفتاح {i+1}: غير موجود أو فارغ")
        failed_keys.append(i+1)
        continue
    
    try:
        genai.configure(api_key=key)
        model = genai.GenerativeModel('gemini-2.0-flash-exp')
        
        response = model.generate_content(
            "مرحبا",
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=10,
            )
        )
        
        print(f"✅ المفتاح {i+1}: يعمل بشكل صحيح")
        working_keys.append(i+1)
        
    except Exception as e:
        error_msg = str(e).lower()
        if "quota" in error_msg or "limit" in error_msg or "resource" in error_msg:
            print(f"⚠️  المفتاح {i+1}: وصل للحد اليومي")
        elif "invalid" in error_msg or "api" in error_msg:
            print(f"❌ المفتاح {i+1}: غير صالح")
        else:
            print(f"❌ المفتاح {i+1}: خطأ - {e}")
        failed_keys.append(i+1)

print(f"\n{'='*50}")
print(f"✅ مفاتيح تعمل: {len(working_keys)}/{len(GEMINI_KEYS)}")
print(f"❌ مفاتيح فاشلة: {len(failed_keys)}/{len(GEMINI_KEYS)}")

if len(working_keys) > 0:
    print(f"\n✅ البوت جاهز للعمل بـ {len(working_keys)} مفتاح")
else:
    print(f"\n❌ تحذير: لا يوجد مفاتيح صالحة!")
    print("تأكد من:")
    print("1. المفاتيح صحيحة في ملف .env")
    print("2. لم تصل لحدها اليومي")
    print("3. مفعلة في Google AI Studio")
