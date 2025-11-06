import os
from flask import Flask, request
from linebot import LineBotApi
from linebot.models import TextSendMessage
from dotenv import load_dotenv
import google.generativeai as genai

# تحميل المتغيرات البيئية
load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-exp")

generation_config = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 2000,
}

def generate_ai_reply(user_text):
    prompt = f"""
أنت صديقة ودودة وحنونة، تتكلم بالعربية العامية السعودية، مختصرة جداً (سطرين أو ثلاثة)، عاطفية وواقعية.
المستخدم قال: "{user_text}"

قواعد مهمة:
- الردود مختصرة وسطرين أو ثلاثة فقط
- بدون أي إيموجي أو رموز
- ودود وعاطفي وحبّي
- افهم شعوره ورد بطريقة صادقة وواقعية
- كل الردود من AI مباشرة، لا تستخدم أي نصوص جاهزة أو بدائل

رد فقط بالرسالة، بدون مقدمات.
"""
    response = model.generate_content(prompt, generation_config=generation_config)
    return response.text.strip()

@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_json()
    if not body:
        return "No JSON received", 400

    try:
        for event in body.get("events", []):
            if event["type"] == "message" and event["message"]["type"] == "text":
                user_text = event["message"]["text"]
                reply_token = event["replyToken"]
                ai_reply = generate_ai_reply(user_text)
                line_bot_api.reply_message(reply_token, TextSendMessage(text=ai_reply))
    except Exception as e:
        print(f"Error processing event: {e}")

    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return "LINE AI LoveBot is running!", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
