# app.py
import os
from flask import Flask, request, jsonify
from google import genai
import random
import time

app = Flask(__name__)

# ===== إعداد المفاتيح الثلاثة =====
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3")
]

if not all(GEMINI_KEYS):
    raise Exception("⚠️ يجب تعيين جميع مفاتيح GEMINI_KEY_1, 2, 3 في Environment")

# مؤشر المفتاح الحالي
current_key_index = 0

# ===== دالة للحصول على المفتاح التالي =====
def get_next_key():
    global current_key_index
    key = GEMINI_KEYS[current_key_index]
    current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
    return key

# ===== دالة إرسال الطلب إلى Gemini مع المحاولة التلقائية =====
def send_to_gemini(prompt, max_retries=None):
    if max_retries is None:
        max_retries = len(GEMINI_KEYS)
    
    last_error = None

    for _ in range(max_retries):
        api_key = get_next_key()
        client = genai.Client(api_key=api_key)
        try:
            response = client.responses.create(
                model="gemini-1.5",
                input=prompt
            )
            return response.output_text
        except Exception as e:
            # إذا انتهى الحد اليومي أو أي خطأ، نحفظ الخطأ ونجرب المفتاح التالي
            last_error = e
            continue

    # إذا لم تنجح أي محاولة، نرجع الخطأ النهائي
    raise last_error

# ===== واجهة Chat =====
@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    prompt = data.get("prompt", "")
    
    if not prompt:
        return jsonify({"error": "لا يوجد نص للإرسال"}), 400

    try:
        answer = send_to_gemini(prompt)
        return jsonify({"response": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===== صفحة رئيسية =====
@app.route("/", methods=["GET"])
def index():
    return "🔥 تطبيق Gemini جاهز ويعمل مع تبديل المفاتيح تلقائيًا!"

# ===== تشغيل التطبيق =====
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
