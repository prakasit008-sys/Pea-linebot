import os
import time
import uuid
import threading
from datetime import datetime

import requests
from flask import Flask, request, abort, send_from_directory

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# ===== LINE TOKEN =====
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ===== BASE URL (ใช้ส่งลิงก์ไฟล์กลับไป) =====
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

# ===== MiniMax =====
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY")
MINIMAX_GROUP_ID = os.getenv("MINIMAX_GROUP_ID")  # เอาจากหน้า Your Profile > GroupID

# โฟลเดอร์เก็บไฟล์ชั่วคราวบน Render
AUDIO_DIR = "/tmp/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

# ===== ฟังก์ชันวันที่ไทย =====
THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน",
    "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม",
    "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
]

def thai_date(d):
    year_th = d.year + 543
    return f"{d.day} {THAI_MONTHS[d.month]} {year_th}"

def build_outage_template(date_text):
    return (
        "📢 งานดับไฟแผนกปฏิบัติการ\n\n"
        "📅 วันพฤหัสบดีที่ 12 กุมภาพันธ์ 2569\n"
        "⏰ เวลา 08:30 - 17:00 น.\n"
        "📍 ดับตั้งแต่ คอตีนสะพานร.ร.บ้านหว้ากอมิตรภาพ ถึง ปากทางหว้าโทนถนนเพชรเกษม\n"
        "****************************************************\n"
        "📅 วันศุกร์ที่ 13 กุมภาพันธ์ 2569\n"
        "⏰ เวลา 08:30 - 17:00 น.\n"
        "📍 ดับตั้งแต่ ร้านไทยถาวรต้นเกตุยาวไปถึง SF6 ไร่คล่องฝั่งขาขึ้นกรุงเทพ\n"
        "*****************************************************\n"
        "📅 วันศุกร์ที่ 20 กุมภาพันธ์ 2569\n"
        "⏰ เวลา 08:30 - 17:00 น.\n"
        "📍 ดับตั้งแต่ สวนขวัญ ตลาดนัดสวนขวัญ โรงนมสวนขวัญ และปั้ม PT"
    )

# ===== Route: เช็คเซิร์ฟเวอร์ =====
@app.route("/", methods=["GET"])
def home():
    return "OK", 200

# ===== Route: เสิร์ฟไฟล์เสียง =====
@app.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    # ส่งไฟล์จาก /tmp/audio
    return send_from_directory(AUDIO_DIR, filename, as_attachment=True)

# ===== LINE CALLBACK =====
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# ===== MiniMax helpers =====
def minimax_create_task(text: str) -> str:
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY not set")
    if not MINIMAX_GROUP_ID:
        raise RuntimeError("MINIMAX_GROUP_ID not set")

    url = f"https://api.minimax.io/v1/t2a_async_v2?GroupId={MINIMAX_GROUP_ID}"  # GroupId ใช้เป็น query param
    payload = {
        "model": "speech-2.8-turbo",        # เปลี่ยนเป็น speech-2.8-hd ได้
        "text": text,
        "language_boost": "auto",
        "voice_setting": {
            "voice_id": "กม7-แยกวังมะเดื่อ",      # ถ้าไม่มี ให้เปลี่ยนเป็น voice_id ที่คุณมีใน System Voice ID List
            "speed": 1,
            "vol": 10,
            "pitch": 1
        },
        "audio_setting": {
            "audio_sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1
        }
    }
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()

    # โครงสร้าง response อาจต่างกันได้ เลยดึงแบบกันพลาด
    task_id = data.get("task_id") or data.get("data", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"Cannot find task_id in response: {data}")
    return task_id

def minimax_poll_file_id(task_id: str, timeout_sec: int = 120) -> str:
    url = f"https://api.minimax.io/v1/query/t2a_async_query_v2?task_id={task_id}"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "content-type": "application/json",
    }

    start = time.time()
    while time.time() - start < timeout_sec:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()

        # พยายามหา file_id จากหลายแบบ
        file_id = (
            data.get("file_id")
            or data.get("data", {}).get("file_id")
            or data.get("data", {}).get("result", {}).get("file_id")
        )
        if file_id:
            return file_id

        # ถ้ายังไม่เสร็จ รอแล้ววนใหม่
        time.sleep(2)

    raise TimeoutError("MiniMax TTS timeout while waiting for file_id")

def minimax_download_mp3(file_id: str) -> bytes:
    url = f"https://api.minimax.io/v1/files/retrieve_content?file_id={file_id}"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "content-type": "application/json",
    }
    r = requests.get(url, headers=headers, timeout=120)
    r.raise_for_status()
    return r.content

def tts_background_job(user_id: str, text: str):
    try:
        task_id = minimax_create_task(text)
        file_id = minimax_poll_file_id(task_id, timeout_sec=180)
        mp3_bytes = minimax_download_mp3(file_id)

        fname = f"{uuid.uuid4().hex}.mp3"
        fpath = os.path.join(AUDIO_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(mp3_bytes)

        if not BASE_URL:
            msg = f"✅ ทำเสียงเสร็จแล้ว แต่ยังไม่ได้ตั้ง BASE_URL จึงส่งลิงก์ไม่ได้ (ไฟล์อยู่ที่เซิร์ฟเวอร์ชื่อ {fname})"
        else:
            dl_url = f"{BASE_URL}/audio/{fname}"
            msg = f"✅ ทำเสียงเสร็จแล้ว 🎧\nดาวน์โหลดไฟล์: {dl_url}"

        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ ทำเสียงไม่สำเร็จ: {e}"))

# ===== รับข้อความ =====
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = (event.message.text or "").strip()

    # --- คำสั่งทำเสียง ---
    # ใช้: "เสียง ข้อความ..."
    if user_text.startswith("เสียง"):
        text = user_text.replace("เสียง", "", 1).strip()
        if not text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="พิมพ์แบบนี้ครับ: เสียง สวัสดีครับ ..."))
            return

        # ตอบทันที แล้วค่อย push ลิงก์ไฟล์ตามหลัง
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⏳ กำลังสร้างเสียงด้วย MiniMax... เดี๋ยวส่งลิงก์ไฟล์ให้ครับ")
        )

        user_id = getattr(event.source, "user_id", None)
        if user_id:
            threading.Thread(target=tts_background_job, args=(user_id, text), daemon=True).start()
        return

    # --- ประกาศดับไฟ ---
    if user_text == "ดับไฟ":
        today = thai_date(datetime.now())
        reply_text = build_outage_template(today)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # --- อย่างอื่นไม่ตอบ ---
    return

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

