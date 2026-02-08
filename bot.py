import os
import time
import uuid
import threading
from datetime import datetime

import requests
from flask import Flask, request, abort, send_file

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, AudioSendMessage  # ✅ เพิ่ม AudioSendMessage

app = Flask(__name__)

# =======================
# ENV
# =======================
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # เช่น https://pea-linebot.onrender.com

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

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
CURRENT_VOICE_ID = os.getenv("DEFAULT_VOICE_ID", "English_expressive_narrator")

# =======================
# Routes
# =======================
@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    fpath = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(fpath):
        abort(404)

    # ✅ แก้: ไม่บังคับดาวน์โหลด เพื่อให้ LINE เล่นได้
    return send_file(
        fpath,
        mimetype="audio/mpeg",
        as_attachment=False,   # ✅ เปลี่ยน True -> False
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
# MiniMax (Sync T2A HTTP)
# =======================
def _require_minimax():
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY not set")

def _minimax_headers():
    return {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }

def _clean_text_for_tts(text: str) -> str:
    # กันตัวอักษรแปลกที่ทำให้ TTS แป๊ก
    return text.replace("\ufeff", "").replace("\u200b", "").strip()

def minimax_t2a_sync(text: str, voice_id: str) -> bytes:
    """
    เรียก MiniMax T2A HTTP (Sync) แล้วได้เสียงกลับมาทันที
    response ตาม docs: {"data": {"audio": "<hex encoded audio>", "status": ...}, ...}
    """
    _require_minimax()

    url = "https://api.minimax.io/v1/t2a_v2"

    payload = {
        "model": "speech-2.8-hd",
        "text": _clean_text_for_tts(text),
        "stream": False,
        "language_boost": "Thai",
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 0.9,
            "vol": 1.2,
            "pitch": -1
        },
        "audio_setting": {
            "audio_sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 2
        }
    }

    r = requests.post(url, headers=_minimax_headers(), json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    # ถ้ามี base_resp ก็เช็ค
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0, "0"):
        raise RuntimeError(f"MiniMax error {base_resp.get('status_code')}: {base_resp.get('status_msg')}")

    audio_hex = (data.get("data") or {}).get("audio")
    if not audio_hex:
        # แสดงตัวอย่างสั้น ๆ ช่วย debug
        raise RuntimeError(f"MiniMax did not return audio hex. Response: {str(data)[:600]}")

    # ✅ แปลง hex -> bytes (ได้ mp3 bytes)
    try:
        return bytes.fromhex(audio_hex)
    except Exception as e:
        raise RuntimeError(f"Failed to decode audio hex: {e}")

def minimax_get_voice_list() -> dict:
    _require_minimax()
    url = "https://api.minimax.io/v1/get_voice"
    r = requests.post(url, headers=_minimax_headers(), json={"voice_type": "all"}, timeout=60)
    r.raise_for_status()
    return r.json()

# =======================
# Background job (Sync call แต่ทำใน thread กัน webhook timeout)
# =======================
def tts_background_job(target_id: str, text: str, voice_id: str):
    try:
        mp3_bytes = minimax_t2a_sync(text, voice_id=voice_id)

        fname = f"{uuid.uuid4().hex}.mp3"
        fpath = os.path.join(AUDIO_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(mp3_bytes)

        if not BASE_URL:
            msg = (
                "✅ ทำเสียงเสร็จแล้ว 🎧\n"
                f"แต่ยังไม่ได้ตั้ง BASE_URL เลยส่งเสียงใน LINE ไม่ได้ (ไฟล์ชื่อ {fname})\n"
                "ให้ไปตั้ง BASE_URL ใน Render Environment แล้ว deploy ใหม่"
            )
            line_bot_api.push_message(target_id, TextSendMessage(text=msg))
            return

        audio_url = f"{BASE_URL}/audio/{fname}"  # ✅ บรรทัดเดียว

        # ✅ ส่งเป็น Audio message: กดฟังใน LINE ได้ทันที
        line_bot_api.push_message(
            target_id,
            AudioSendMessage(
                original_content_url=audio_url,
                duration=30000  # ถ้าอยากให้แม่นยำ เดี๋ยวเพิ่ม mutagen ได้
            )
        )

        # ✅ ถ้าอยากให้มีลิงก์โหลดด้วย (เปิดได้/แชร์ได้) ให้ปลดคอมเมนต์
        # line_bot_api.push_message(target_id, TextSendMessage(text=f"ดาวน์โหลดไฟล์ MP3: {audio_url}"))

    except Exception as e:
        line_bot_api.push_message(target_id, TextSendMessage(text=f"❌ ทำเสียงไม่สำเร็จ: {e}"))

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

    # ส่งกลับไปที่ user/group/room ให้ถูก
    target_id = getattr(event.source, "user_id", None) \
        or getattr(event.source, "group_id", None) \
        or getattr(event.source, "room_id", None)

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
            for i, v in enumerate(voices[:50], 1):
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

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"⏳ กำลังสร้างเสียงด้วย MiniMax (Sync HTTP)...\nVOICE: {CURRENT_VOICE_ID}\nเสร็จแล้วจะส่งเสียงให้ฟังใน LINE ครับ")
        )

        if not target_id:
            return

        threading.Thread(
            target=tts_background_job,
            args=(target_id, text, CURRENT_VOICE_ID),
            daemon=True
        ).start()
        return

    return

# =======================
# Main
# =======================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
