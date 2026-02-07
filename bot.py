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

# =============================
# LINE
# =============================
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    print("⚠️ LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET ยังไม่ถูกตั้งค่าใน ENV")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# =============================
# BASE URL (โดเมนเว็บบอทของคุณ เช่น https://xxx.onrender.com)
# ใช้ส่งลิงก์ไฟล์ mp3 กลับไปใน LINE
# =============================
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

# =============================
# MiniMax
# =============================
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.getenv("MINIMAX_GROUP_ID", "")

# ✅ voice_id ที่ใช้งานจริง (ต้องเป็น ID จริง ไม่ใช่ชื่อ)
# ตั้งได้ด้วย ENV หรือใช้ /setvoice ใน LINE
VOICE_ID = os.getenv("VOICE_ID", "").strip()

# โฟลเดอร์เก็บไฟล์ mp3 ชั่วคราวบน Render
AUDIO_DIR = "/tmp/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

# =============================
# รูปแบบประกาศดับไฟ (ตามที่คุณให้มา)
# =============================
def build_outage_template():
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

# =============================
# Routes
# =============================
@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename, as_attachment=True)

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

# =============================
# MiniMax helpers
# =============================
def _minimax_headers():
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY not set")
    return {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }

def minimax_create_task(text: str) -> str:
    """
    สร้าง task TTS
    """
    if not MINIMAX_GROUP_ID:
        raise RuntimeError("MINIMAX_GROUP_ID not set")

    # ❗ต้องมี VOICE_ID ที่ถูกต้อง
    if not VOICE_ID:
        raise RuntimeError("VOICE_ID ยังว่าง -> พิมพ์ /voices แล้วใช้ /setvoice <voice_id>")

    url = f"https://api.minimax.io/v1/t2a_async_v2?GroupId={MINIMAX_GROUP_ID}"

    payload = {
        "model": "speech-2.8-hd",
        "text": text,
        "language_boost": "auto",
        "voice_setting": {
            "voice_id": VOICE_ID,
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

    r = requests.post(url, headers=_minimax_headers(), json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()

    task_id = data.get("task_id") or data.get("data", {}).get("task_id")
    if not task_id or str(task_id) == "0":
        raise RuntimeError(f"Cannot find task_id in response: {data}")

    return str(task_id)

def minimax_poll_file_id(task_id: str, timeout_sec: int = 180) -> str:
    """
    รอจนได้ file_id
    """
    url = f"https://api.minimax.io/v1/query/t2a_async_query_v2?task_id={task_id}"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}", "content-type": "application/json"}

    start = time.time()
    while time.time() - start < timeout_sec:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()

        file_id = (
            data.get("file_id")
            or data.get("data", {}).get("file_id")
            or data.get("data", {}).get("result", {}).get("file_id")
        )
        if file_id:
            return str(file_id)

        time.sleep(2)

    raise TimeoutError("MiniMax TTS timeout while waiting for file_id")

def minimax_download_mp3(file_id: str) -> bytes:
    """
    ดาวน์โหลด mp3 bytes
    """
    url = f"https://api.minimax.io/v1/files/retrieve_content?file_id={file_id}"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}", "content-type": "application/json"}
    r = requests.get(url, headers=headers, timeout=120)
    r.raise_for_status()
    return r.content

# ==========================================================
# ✅ 1) ฟังก์ชันที่คุณสั่งให้ใส่ (Get Voice List)
# วางต่อจาก minimax_download_mp3(...) ตามที่คุณต้องการ
# ==========================================================
def minimax_get_voice_list() -> dict:
    url = "https://api.minimax.io/v1/get_voice"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"voice_type": "all"}  # เอาทุกประเภท
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

# =============================
# Background job: สร้างเสียงแล้ว push ลิงก์ไฟล์
# =============================
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
            msg = (
                "✅ ทำเสียงเสร็จแล้ว 🎧\n"
                f"แต่ยังไม่ได้ตั้ง BASE_URL จึงส่งลิงก์ไม่ได้\n"
                f"(ไฟล์อยู่ที่เซิร์ฟเวอร์ชื่อ {fname})"
            )
        else:
            dl_url = f"{BASE_URL}/audio/{fname}"
            msg = f"✅ ทำเสียงเสร็จแล้ว 🎧\nดาวน์โหลดไฟล์: {dl_url}"

        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ ทำเสียงไม่สำเร็จ: {e}"))

# =============================
# LINE handler
# =============================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    global VOICE_ID

    user_text = (event.message.text or "").strip()

    # ==========================================================
    # ✅ 2) คำสั่ง /voices (ใส่บนสุด ก่อน if อื่น ๆ) ตามที่คุณสั่ง
    # ==========================================================
    if user_text.strip().lower() == "/voices":
        try:
            data = minimax_get_voice_list()

            # รวมรายการเสียงจากหลาย key กัน schema ไม่เหมือน
            voices = []
            for key in ["system_voice", "voice_cloning", "voice_generation", "voices", "data"]:
                v = data.get(key) if isinstance(data, dict) else None
                if isinstance(v, list):
                    voices += v
                elif isinstance(v, dict) and isinstance(v.get("voices"), list):
                    voices += v["voices"]

            if not voices:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"ไม่พบรายการเสียง:\n{str(data)[:1500]}")
                )
                return

            lines = []
            for i, v in enumerate(voices[:10], 1):
                vid = v.get("voice_id") or v.get("id") or v.get("voiceId")
                name = v.get("name") or v.get("voice_name") or v.get("title")
                lines.append(f"{i}. {name}\nvoice_id: {vid}")

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="รายการเสียง (10 รายการแรก):\n" + "\n".join(lines))
            )
            return

        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"ดึงรายการเสียงไม่สำเร็จ: {e}")
            )
            return

    # ✅ ตั้งเสียงโดยไม่ต้องแก้โค้ด
    if user_text.lower().startswith("/setvoice"):
        parts = user_text.split(maxsplit=1)
        if len(parts) < 2:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ใช้แบบนี้: /setvoice <voice_id>"))
            return
        VOICE_ID = parts[1].strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ตั้งค่า VOICE_ID แล้ว ✅\n{VOICE_ID}"))
        return

    if user_text.lower() == "/voice":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"VOICE_ID ปัจจุบัน: {VOICE_ID or '(ว่าง)'}"))
        return

    if user_text.lower() == "/help":
        msg = (
            "คำสั่ง:\n"
            "/voices = ดูรายการเสียง + voice_id (10 รายการแรก)\n"
            "/setvoice <voice_id> = ตั้งเสียง\n"
            "/voice = ดูเสียงที่ตั้งอยู่\n\n"
            "ใช้งาน:\n"
            "ดับไฟ = ส่งประกาศดับไฟ\n"
            "เสียง <ข้อความ> = ทำเสียงจากข้อความ\n"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

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
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ หา user_id ไม่เจอ (ใช้ในกลุ่มอาจต้องเปิดให้บอทเห็น userId)"))
        return

    # --- ประกาศดับไฟ ---
    if user_text == "ดับไฟ":
        reply_text = build_outage_template()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # --- อย่างอื่น ---
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="พิมพ์ /help เพื่อดูคำสั่ง"))
    return

# =============================
# Start server
# =============================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
