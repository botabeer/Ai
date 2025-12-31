import os, re, time, sqlite3, threading, logging, warnings
from queue import Queue
from datetime import datetime
from collections import defaultdict

from flask import Flask, request, abort, jsonify
from dotenv import load_dotenv

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent

warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
import google.generativeai as genai

# ---------------- INIT ----------------
load_dotenv()
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------- ENV ----------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

MODEL_NAME = "gemini-2.0-flash-exp"
PORT = int(os.getenv("PORT", 5000))

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise RuntimeError("Missing LINE credentials")
if not GEMINI_KEYS:
    raise RuntimeError("Missing Gemini keys")

# ---------------- LINE ----------------
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ---------------- DB ----------------
DB_PATH = "chatbot.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id TEXT PRIMARY KEY,
        daily_count INTEGER,
        daily_reset TEXT
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS chats(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        role TEXT,
        content TEXT,
        ts TEXT
    )""")
    db.commit()
    db.close()

init_db()

# ---------------- RATE LIMIT ----------------
class RateLimiter:
    def __init__(self):
        self.data = defaultdict(list)

    def allow(self, uid, seconds=2):
        now = time.time()
        self.data[uid] = [t for t in self.data[uid] if now - t < 60][-5:]
        if self.data[uid] and now - self.data[uid][-1] < seconds:
            return False
        self.data[uid].append(now)
        return True

rate_limiter = RateLimiter()

# ---------------- GEMINI ----------------
GEN_CONFIG = {
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "max_output_tokens": 400
}

SAFETY = [{"category": c, "threshold": "BLOCK_NONE"} for c in [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT"
]]

key_index = 0
def next_key():
    global key_index
    key = GEMINI_KEYS[key_index]
    key_index = (key_index + 1) % len(GEMINI_KEYS)
    return key

# ---------------- AI (صديقة داعمة مختصرة) ----------------
def ai_reply(user_text):
    system_prompt = """
أنتِ صديقة قريبة وحكيمة.
كلامك:
- مختصر
- صادق
- بدون إيموجي
- يوجّه للصح بهدوء

قواعد:
- 1 إلى 3 جمل فقط
- لا وعظ
- لا مبالغة
- استخدمي صيغة المؤنث
"""

    prompt = f"""{system_prompt}

كلام المستخدم:
{user_text}

رد الصديقة:
"""

    for _ in range(3):
        try:
            genai.configure(api_key=next_key())
            model = genai.GenerativeModel(
                MODEL_NAME,
                generation_config=GEN_CONFIG,
                safety_settings=SAFETY
            )
            res = model.generate_content(prompt, request_options={"timeout": 8})
            if res and res.text:
                return re.sub(r"\s+", " ", res.text.strip())
        except Exception:
            time.sleep(0.3)

    return "خلينا نوقف لحظة ونفكر بهدوء."

# ---------------- QUEUE + WORKERS ----------------
queue = Queue()

def worker():
    while True:
        uid, text = queue.get()
        try:
            reply = ai_reply(text)

            db = get_db()
            db.execute(
                "INSERT INTO chats VALUES(NULL, ?, 'assistant', ?, ?)",
                (uid, reply, datetime.now().isoformat())
            )
            db.commit()
            db.close()

            with ApiClient(configuration) as api:
                MessagingApi(api).push_message(
                    PushMessageRequest(to=uid, messages=[TextMessage(text=reply)])
                )
        finally:
            queue.task_done()

for _ in range(3):
    threading.Thread(target=worker, daemon=True).start()

# ---------------- LINE EVENTS ----------------
@handler.add(FollowEvent)
def follow(event):
    with ApiClient(configuration) as api:
        MessagingApi(api).push_message(
            PushMessageRequest(
                to=event.source.user_id,
                messages=[TextMessage(text="أهلًا فيك. أنا هنا أسمعك وأساعدك.")]
            )
        )

@handler.add(MessageEvent, message=TextMessageContent)
def message(event):
    uid = event.source.user_id
    text = event.message.text.strip()
    if not text or not rate_limiter.allow(uid):
        return

    db = get_db()
    today = datetime.now().date().isoformat()
    row = db.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

    if row:
        if row["daily_reset"] != today:
            db.execute(
                "UPDATE users SET daily_count=1, daily_reset=? WHERE user_id=?",
                (today, uid)
            )
        else:
            db.execute(
                "UPDATE users SET daily_count=daily_count+1 WHERE user_id=?",
                (uid,)
            )
    else:
        db.execute(
            "INSERT INTO users VALUES (?, 1, ?)",
            (uid, today)
        )

    db.execute(
        "INSERT INTO chats VALUES(NULL, ?, 'user', ?, ?)",
        (uid, text, datetime.now().isoformat())
    )
    db.commit()
    db.close()

    queue.put((uid, text))

    with ApiClient(configuration) as api:
        MessagingApi(api).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="تمام، خليني أفكر.")]
            )
        )

# ---------------- ROUTES ----------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "running"})

@app.route("/callback", methods=["POST"])
def callback():
    sig = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, sig)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
