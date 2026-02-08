import os
import uuid
import threading
from datetime import datetime

import requests
from flask import Flask, request, abort, send_file

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, AudioSendMessage

app = Flask(__name__)

# =======================
# ENV
# =======================
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
BASE_URL = os.getenv("BASE_URL", "").strip().rstrip("/")  # เช่น https://pea-linebot.onrender.com
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "").strip()

# เสียงเริ่มต้น (แนะนำใช้เสียงไทย หรือใส่เสียงโคลนของคุณ)
CURRENT_VOICE_ID = os.getenv("DEFAULT_VOICE_ID", "Thai_female_1_sample1").strip()

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
# ข้อความดับไฟ (ตามที่คุณให้มา)
# =======================
OUTAGE_TEXT = (
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
    "📍 ดับตั้งแต่ สวนขวัญ ตลาดนัดสวนขวัญ โรงนมสวนขวัญ และปั้ม PT\n"
)

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

    # สำคัญ: ห้าม as_attachment=True ไม่งั้นจะกลายเป็นดาวน์โหลด
    # ให้ส่งเป็น audio/mpeg เพื่อให้ LINE เล่นได้
    return send_file(
        fpath,
        mimetype="audio/mpeg",
        as_attachment=False,
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
# MiniMax (Sync T2A HTTP -> hex -> bytes)
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
    return text.replace("\ufeff", "").replace("\u200b", "").strip()

def minimax_t2a_sync(text: str, voice_id: str) -> tuple[bytes, int]:
    """
    คืนค่า: (mp3_bytes, duration_ms)
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
            "speed": 0.95,
            "vol": 1.1,
            "pitch": 0
        },
        "audio_setting": {
            "audio_sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1
        }
    }

    r = requests.post(url, headers=_minimax_headers(), json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0, "0"):
        raise RuntimeError(f"MiniMax error {base_resp.get('status_code')}: {base_resp.get('status_msg')}")

    audio_hex = (data.get("data") or {}).get("audio")
    if not audio_hex:
        raise RuntimeError(f"MiniMax did not return audio hex. Response: {str(data)[:600]}")

    try:
        mp3_bytes = bytes.fromhex(audio_hex)
    except Exception as e:
        raise RuntimeError(f"Failed to decode audio hex: {e}")

    duration_ms = int((data.get("extra_info") or {}).get("audio_length") or 0)
    if duration_ms <= 0:
        duration_ms = 30000

    return mp3_bytes, duration_ms

# =======================
# ส่งเสียงแบบกดเล่นใน LINE (AudioSendMessage)
# =======================
def push_audio(target_id: str, mp3_filename: str, duration_ms: int):
    if not BASE_URL:
        # ถ้าไม่มี BASE_URL จะทำ Audio message ไม่ได้
        line_bot_api.push_message(
            target_id,
            TextSendMessage(text="❌ ยังไม่ได้ตั้งค่า BASE_URL (ต้องเป็น https://... ) เพื่อให้ LINE เล่นเสียงได้")
        )
        return

    audio_url = f"{BASE_URL}/audio/{mp3_filename}"  # ต้องเป็นบรรทัดเดียว
    line_bot_api.push_message(
        target_id,
        AudioSendMessage(
            original_content_url=audio_url,
            duration=duration_ms
        )
    )

# =======================
# Background job (สร้าง mp3 แล้ว push เป็น Audio)
# =======================
def tts_background_job(target_id: str, text: str, voice_id: str):
    try:
        mp3_bytes, duration_ms = minimax_t2a_sync(text, voice_id=voice_id)

        fname = f"{uuid.uuid4().hex}.mp3"
        fpath = os.path.join(AUDIO_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(mp3_bytes)

        push_audio(target_id, fname, duration_ms)

    except Exception as e:
        line_bot_api.push_message(
            target_id,
            TextSendMessage(text=f"❌ ทำเสียงไม่สำเร็จ: {e}")
        )

# =======================
# LINE Message Handler (ตอบแค่ ดับไฟ / ทำเสียง)
# =======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    global CURRENT_VOICE_ID

    user_text = (event.message.text or "").strip()

    # target id สำหรับ push (group/user/room)
    target_id = getattr(event.source, "user_id", None) \
        or getattr(event.source, "group_id", None) \
        or getattr(event.source, "room_id", None)

    # 1) ดับไฟ -> ส่งเสียงประกาศ
    if user_text == "ดับไฟ":
        # ตอบทันทีว่าเริ่มทำแล้ว (กันเงียบ)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⏳ กำลังสร้างเสียงประกาศดับไฟ...")
        )
        if target_id:
            threading.Thread(
                target=tts_background_job,
                args=(target_id, OUTAGE_TEXT, CURRENT_VOICE_ID),
                daemon=True
            ).start()
        return

    # 2) ทำเสียง <ข้อความ>
    if user_text.startswith("ทำเสียง"):
        speak_text = user_text.replace("ทำเสียง", "", 1).strip()
        if not speak_text:
            # ไม่ตอบอย่างอื่น แต่กรณีนี้ควรบอกวิธีพิมพ์
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="พิมพ์แบบนี้ครับ: ทำเสียง สวัสดีครับ ...")
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⏳ กำลังสร้างเสียง...")
        )

        if target_id:
            threading.Thread(
                target=tts_background_job,
                args=(target_id, speak_text, CURRENT_VOICE_ID),
                daemon=True
            ).start()
        return

    # ไม่ตอบอย่างอื่น
    return

# =======================
# Main
# =======================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
