"""
🔧 اكتشاف النماذج المتوفرة
============================
يعرض جميع نماذج Gemini المتاحة حالياً
"""

import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

print("🔍 جاري فحص النماذج المتوفرة...\n")

# استخدم أول مفتاح
api_key = os.getenv('GEMINI_API_KEY_1')
if not api_key:
    print("❌ لا يوجد GEMINI_API_KEY_1 في .env")
    exit(1)

try:
    genai.configure(api_key=api_key)
    
    print("📋 النماذج المتوفرة لـ generateContent:\n")
    print(f"{'اسم النموذج':<45} {'الحالة'}")
    print("="*60)
    
    models_list = genai.list_models()
    working_models = []
    
    for m in models_list:
        model_name = m.name.replace('models/', '')
        
        # فقط النماذج اللي تدعم generateContent
        if 'generateContent' in m.supported_generation_methods:
            # جرب النموذج
            try:
                test_model = genai.GenerativeModel(model_name)
                response = test_model.generate_content(
                    "Hi",
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=5,
                    )
                )
                print(f"{model_name:<45} ✅ يعمل")
                working_models.append(model_name)
            except Exception as e:
                if "404" in str(e):
                    print(f"{model_name:<45} ❌ غير متوفر")
                else:
                    print(f"{model_name:<45} ⚠️ خطأ")
    
    print("\n" + "="*60)
    print(f"\n✅ النماذج التي تعمل: {len(working_models)}")
    
    if working_models:
        print("\n💡 النماذج الموصى بها بالترتيب:\n")
        
        # ترتيب حسب الأفضلية
        priority = ['gemini-1.5-flash-002', 'gemini-1.5-flash', 
                   'gemini-1.5-flash-8b', 'gemini-pro']
        
        recommended = []
        for p in priority:
            for m in working_models:
                if p in m and m not in recommended:
                    recommended.append(m)
                    break
        
        # أضف الباقي
        for m in working_models:
            if m not in recommended:
                recommended.append(m)
        
        for i, model in enumerate(recommended[:5], 1):
            print(f"  {i}. {model}")
        
        print(f"\n📝 عدّل في app.py:")
        print(f"   model = genai.GenerativeModel('{recommended[0]}')")
    else:
        print("\n❌ لم نجد أي نموذج يعمل!")
        print("   تحقق من:")
        print("   1. المفتاح صحيح")
        print("   2. لم يصل للحد اليومي")
        print("   3. حدثت المكتبة: pip install -U google-generativeai")
    
    print("\n" + "="*60)
    print(f"📦 إصدار المكتبة: {genai.__version__}")
    
except Exception as e:
    print(f"❌ خطأ: {e}")
    print("\n💡 جرب:")
    print("   pip install --upgrade google-generativeai")
