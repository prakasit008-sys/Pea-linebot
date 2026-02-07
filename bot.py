import os
import time
import uuid
import threading
from datetime import datetime

import requests
from flask import Flask, request, abort, send_file

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# =======================
# ENV
# =======================
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # เช่น https://pea-linebot.onrender.com

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.getenv("MINIMAX_GROUP_ID", "")  # จาก MiniMax profile (GroupId)

# =======================
# LINE
# =======================
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# =======================
# Storage (Render: /tmp)
# =======================
AUDIO_DIR = "/tmp/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

# =======================
# Thai date helpers
# =======================
THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน",
    "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม",
    "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
]

def thai_date(d: datetime) -> str:
    year_th = d.year + 543
    return f"{d.day} {THAI_MONTHS[d.month]} {year_th}"

def build_outage_template() -> str:
    # ตามข้อความล่าสุดที่คุณให้
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

# =======================
# Default voice (ปรับได้ด้วย /setvoice)
# =======================
CURRENT_VOICE_ID = os.getenv("DEFAULT_VOICE_ID", "English_CalmWoman")

# =======================
# Routes
# =======================
@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    # ส่งเป็น audio/mpeg ให้ชัวร์
    fpath = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(fpath):
        abort(404)
    return send_file(
        fpath,
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=filename
    )

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

# =======================
# MiniMax helpers
# =======================
def _require_minimax():
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY not set")
    if not MINIMAX_GROUP_ID:
        raise RuntimeError("MINIMAX_GROUP_ID not set")
    if not BASE_URL:
        # ไม่บังคับ แต่เตือนในผลลัพธ์ตอนส่งลิงก์
        pass

def minimax_get_voice_list() -> dict:
    _require_minimax()
    url = "https://api.minimax.io/v1/get_voice"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"voice_type": "all"}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

def minimax_create_task(text: str, voice_id: str) -> str:
    _require_minimax()

    url = f"https://api.minimax.io/v1/t2a_async_v2?GroupId={MINIMAX_GROUP_ID}"
    payload = {
        "model": "speech-2.8-hd",  # เปลี่ยนได้
        "text": text,
        "language_boost": "auto",
        "voice_setting": {
            "voice_id": voice_id,
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

    # MiniMax บางครั้งจะส่ง task_id=0 พร้อม base_resp แจ้ง error
    base_resp = data.get("base_resp") or {}
    status_code = base_resp.get("status_code")
    status_msg = base_resp.get("status_msg")

    if status_code and int(status_code) != 0:
        raise RuntimeError(f"MiniMax error {status_code}: {status_msg}")

    task_id = data.get("task_id") or (data.get("data") or {}).get("task_id")
    if not task_id or str(task_id) == "0":
        raise RuntimeError(f"Cannot find valid task_id in response: {data}")

    return str(task_id)

def minimax_poll_file_id(task_id: str, timeout_sec: int = 180) -> str:
    _require_minimax()

    url = f"https://api.minimax.io/v1/query/t2a_async_query_v2?task_id={task_id}"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}"}

    start = time.time()
    while time.time() - start < timeout_sec:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()

        base_resp = data.get("base_resp") or {}
        status_code = base_resp.get("status_code")
        status_msg = base_resp.get("status_msg")
        if status_code and int(status_code) != 0:
            raise RuntimeError(f"MiniMax query error {status_code}: {status_msg}")

        file_id = (
            data.get("file_id")
            or (data.get("data") or {}).get("file_id")
            or ((data.get("data") or {}).get("result") or {}).get("file_id")
        )
        if file_id and str(file_id) != "0":
            return str(file_id)

        time.sleep(2)

    raise TimeoutError("MiniMax TTS timeout while waiting for file_id")

def minimax_download_mp3(file_id: str) -> bytes:
    _require_minimax()

    # 1) ขอไฟล์จาก endpoint
    url = f"https://api.minimax.io/v1/files/retrieve_content?file_id={file_id}"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}"}

    r = requests.get(url, headers=headers, timeout=120)
    r.raise_for_status()

    ctype = (r.headers.get("Content-Type") or "").lower()

    # ✅ ถ้าเป็นเสียงเลย
    if "audio" in ctype or "mpeg" in ctype:
        return r.content

    # ❗ ถ้าไม่ใช่ audio อาจเป็น JSON ที่บอก download_url
    try:
        data = r.json()
    except Exception:
        preview = r.text[:300]
        raise RuntimeError(f"Downloaded content is not audio (Content-Type={ctype}). Preview: {preview}")

    dl_url = (
        data.get("download_url")
        or data.get("file_url")
        or data.get("url")
        or (data.get("data") or {}).get("download_url")
        or (data.get("data") or {}).get("file_url")
        or (data.get("data") or {}).get("url")
    )

    if not dl_url:
        raise RuntimeError(f"retrieve_content did not return audio and no download url found: {data}")

    # 2) ไปโหลด mp3 จาก url จริง
    r2 = requests.get(dl_url, timeout=120)
    r2.raise_for_status()

    ctype2 = (r2.headers.get("Content-Type") or "").lower()
    if "audio" not in ctype2 and "mpeg" not in ctype2:
        preview2 = r2.text[:300]
        raise RuntimeError(f"Downloaded URL is not audio (Content-Type={ctype2}). Preview: {preview2}")

    return r2.content

# =======================
# Background job
# =======================
def tts_background_job(user_id: str, text: str, voice_id: str):
    try:
        task_id = minimax_create_task(text, voice_id=voice_id)
        file_id = minimax_poll_file_id(task_id, timeout_sec=180)
        mp3_bytes = minimax_download_mp3(file_id)

        fname = f"{uuid.uuid4().hex}.mp3"
        fpath = os.path.join(AUDIO_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(mp3_bytes)

        if not BASE_URL:
            msg = (
                "✅ ทำเสียงเสร็จแล้ว 🎧\n"
                f"แต่ยังไม่ได้ตั้ง BASE_URL เลยส่งลิงก์ไม่ได้ (ไฟล์ชื่อ {fname})\n"
                "ให้ไปตั้ง BASE_URL ใน Render Environment แล้ว deploy ใหม่"
            )
        else:
            dl_url = f"{BASE_URL}/audio/{fname}"
            msg = f"✅ ทำเสียงเสร็จแล้ว 🎧\nดาวน์โหลดไฟล์ MP3: {dl_url}"

        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ ทำเสียงไม่สำเร็จ: {e}"))

# =======================
# Message handler
# =======================
def _help_text() -> str:
    return (
        "คำสั่งที่ใช้ได้:\n"
        "1) /help = ดูคำสั่ง\n"
        "2) /voices = ดูรายการเสียง (10 ตัวอย่าง)\n"
        "3) /setvoice <voice_id> = ตั้งเสียงที่ใช้\n"
        "4) เสียง <ข้อความ> = สร้างไฟล์ MP3\n"
        "5) ดับไฟ = ส่งประกาศดับไฟ\n\n"
        f"VOICE ปัจจุบัน: {CURRENT_VOICE_ID}"
    )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    global CURRENT_VOICE_ID

    user_text = (event.message.text or "").strip()
    lower = user_text.lower()

    # --- help ---
    if lower == "/help":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=_help_text()))
        return

    # --- voices ---
    if lower == "/voices":
        try:
            data = minimax_get_voice_list()

            voices = []
            if isinstance(data, dict):
                for key in ["system_voice", "voice_cloning", "voice_generation", "voices", "data"]:
                    v = data.get(key)
                    if isinstance(v, list):
                        voices += v
                    elif isinstance(v, dict) and isinstance(v.get("voices"), list):
                        voices += v["voices"]

            if not voices:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"ไม่พบรายการเสียง หรือ schema เปลี่ยน:\n{str(data)[:1500]}")
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
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ดึงรายการเสียงไม่สำเร็จ: {e}"))
            return

    # --- setvoice ---
    if lower.startswith("/setvoice"):
        parts = user_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="วิธีใช้: /setvoice <voice_id>"))
            return
        CURRENT_VOICE_ID = parts[1].strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ตั้งค่า VOICE_ID แล้ว ✅\n{CURRENT_VOICE_ID}"))
        return

    # --- outage ---
    if user_text == "ดับไฟ":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=build_outage_template()))
        return

    # --- tts ---
    if user_text.startswith("เสียง"):
        text = user_text.replace("เสียง", "", 1).strip()
        if not text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="พิมพ์แบบนี้ครับ: เสียง สวัสดีครับ ..."))
            return

        # ตอบกลับทันที
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"⏳ กำลังสร้างเสียงด้วย MiniMax...\nVOICE: {CURRENT_VOICE_ID}\nเดี๋ยวส่งลิงก์ไฟล์ให้ครับ")
        )

        user_id = getattr(event.source, "user_id", None)
        if user_id:
            threading.Thread(
                target=tts_background_job,
                args=(user_id, text, CURRENT_VOICE_ID),
                daemon=True
            ).start()
        else:
            # กรณี group/room บางแบบไม่มี user_id
            line_bot_api.push_message(event.source.group_id, TextSendMessage(text="❌ ไม่พบ user_id สำหรับ push กลับ"))
        return

    # อย่างอื่น: ไม่ตอบ (หรือจะให้ตอบ /help ก็ได้)
    return

# =======================
# Main
# =======================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
