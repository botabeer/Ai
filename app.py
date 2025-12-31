import os
import random
from flask import Flask, request, jsonify

# جلب مفاتيح Gemini من Environment
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3")
]

# تحقق من وجود جميع المفاتيح
if not all(GEMINI_KEYS):
    raise Exception("⚠️ يجب تعيين جميع مفاتيح GEMINI_KEY_1, 2, 3 في Environment")

# عداد لتتبع المفتاح الحالي
current_key_index = 0

app = Flask(__name__)

def get_next_key():
    """إرجاع المفتاح الحالي والتبديل للمفتاح التالي"""
    global current_key_index
    key = GEMINI_KEYS[current_key_index]
    current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
    return key

def ask_gemini_api(prompt, key):
    """
    هذه دالة وهمية تحاكي إرسال الطلب إلى Google Gemini
    ويمكنك استبدالها بالكود الحقيقي لمكتبة google-genai
    """
    # مثال: فشل مفتاح معين بشكل عشوائي لمحاكاة انتهاء quota
    if random.random() < 0.3:  # 30% احتمالية فشل المفتاح
        raise Exception("Quota exceeded for this key")
    return f"رد وهمي على '{prompt}' باستخدام المفتاح {key[-4:]}"

@app.route("/ask", methods=["POST"])
def ask_gemini():
    data = request.json
    prompt = data.get("prompt")
    if not prompt:
        return jsonify({"error": "يجب إرسال prompt"}), 400

    tried_keys = 0
    max_keys = len(GEMINI_KEYS)
    response_text = None

    while tried_keys < max_keys:
        key = get_next_key()
        print(f"🔑 محاولة استخدام المفتاح: {key}")
        try:
            response_text = ask_gemini_api(prompt, key)
            break  # نجح المفتاح، نخرج من الحلقة
        except Exception as e:
            print(f"❌ المفتاح {key} فشل: {str(e)}")
            tried_keys += 1

    if response_text is None:
        return jsonify({"error": "⚠️ جميع المفاتيح الثلاثة انتهى حدها اليومي"}), 503

    return jsonify({"response": response_text})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
