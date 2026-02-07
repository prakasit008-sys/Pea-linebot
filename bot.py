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
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ===== BASE URL (ใช้ส่งลิงก์ไฟล์กลับไป) =====
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

# ===== MiniMax =====
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.getenv("MINIMAX_GROUP_ID", "")

# โฟลเดอร์เก็บไฟล์ชั่วคราวบน Render
AUDIO_DIR = "/tmp/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

# ===== เก็บค่า voice_id ที่ผู้ใช้ตั้งไว้ (แบบง่าย: in-memory) =====
# หมายเหตุ: ถ้า Render restart ค่าอาจหาย (แต่ใช้งานจริงได้ก่อน)
USER_VOICE = {}  # user_id -> voice_id

# ===== ฟังก์ชันวันที่ไทย =====
THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน",
    "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม",
    "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
]

def thai_date(d):
    year_th = d.year + 543
    return f"{d.day} {THAI_MONTHS[d.month]} {year_th}"

def build_outage_template(_date_text: str):
    # ใช้ “ประกาศดับไฟ” ตามที่คุณส่งมา
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


# =========================
# ===== MiniMax helpers =====
# =========================

def _minimax_headers():
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY not set")
    return {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }

def minimax_get_voice_list() -> dict:
    # ตามที่คุณขอ
    url = "https://api.minimax.io/v1/get_voice"
    payload = {"voice_type": "all"}
    r = requests.post(url, headers=_minimax_headers(), json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

def minimax_create_task(text: str, voice_id: str, model: str = "speech-2.8-hd") -> str:
    """
    สร้างงาน TTS แบบ async
    - ถ้าเสียงผิด / เครดิตไม่พอ จะ raise พร้อมข้อความจาก base_resp
    """
    if not MINIMAX_GROUP_ID:
        raise RuntimeError("MINIMAX_GROUP_ID not set")

    url = f"https://api.minimax.io/v1/t2a_async_v2?GroupId={MINIMAX_GROUP_ID}"
    payload = {
        "model": model,
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

    r = requests.post(url, headers=_minimax_headers(), json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()

    # ✅ สำคัญ: อ่าน base_resp ก่อน
    base_resp = data.get("base_resp") if isinstance(data, dict) else None
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code", 0)
        status_msg = base_resp.get("status_msg", "")
        if status_code and status_code != 0:
            # status_code 1008 = insufficient balance
            raise RuntimeError(f"MiniMax error {status_code}: {status_msg}")

    task_id = data.get("task_id") or (data.get("data", {}).get("task_id") if isinstance(data.get("data"), dict) else None)
    if not task_id:
        raise RuntimeError(f"Cannot find task_id in response: {data}")

    return str(task_id)

def minimax_poll_file_id(task_id: str, timeout_sec: int = 180) -> str:
    url = f"https://api.minimax.io/v1/query/t2a_async_query_v2?task_id={task_id}"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }

    start = time.time()
    while time.time() - start < timeout_sec:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()

        # ✅ ถ้ามี base_resp ผิด ก็ฟ้อง
        base_resp = data.get("base_resp") if isinstance(data, dict) else None
        if isinstance(base_resp, dict):
            status_code = base_resp.get("status_code", 0)
            status_msg = base_resp.get("status_msg", "")
            if status_code and status_code != 0:
                raise RuntimeError(f"MiniMax query error {status_code}: {status_msg}")

        file_id = (
            data.get("file_id")
            or (data.get("data", {}).get("file_id") if isinstance(data.get("data"), dict) else None)
            or (data.get("data", {}).get("result", {}).get("file_id") if isinstance(data.get("data"), dict) and isinstance(data["data"].get("result"), dict) else None)
        )
        if file_id:
            return str(file_id)

        time.sleep(2)

    raise TimeoutError("MiniMax TTS timeout while waiting for file_id")

def minimax_download_mp3(file_id: str) -> bytes:
    """
    ✅ แก้ปัญหา “ไฟล์ชื่อ .mp3 แต่ข้างในไม่ใช่เสียง”
    - ถ้า Content-Type ไม่ใช่ audio/* จะ raise
    """
    url = f"https://api.minimax.io/v1/files/retrieve_content?file_id={file_id}"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
    }
    r = requests.get(url, headers=headers, timeout=120)
    r.raise_for_status()

    content_type = (r.headers.get("Content-Type") or "").lower()
    if "audio" not in content_type:
        # ช่วย debug: ตัดข้อความบางส่วนกลับไปให้เห็นว่าได้อะไรมาแทน
        preview = r.text[:500] if r.text else ""
        raise RuntimeError(f"Downloaded content is not audio (Content-Type={content_type}). Preview: {preview}")

    return r.content


# =========================
# ===== Background TTS =====
# =========================

def tts_background_job(user_id: str, text: str, voice_id: str):
    try:
        task_id = minimax_create_task(text=text, voice_id=voice_id, model="speech-2.8-hd")
        file_id = minimax_poll_file_id(task_id, timeout_sec=180)
        mp3_bytes = minimax_download_mp3(file_id)

        fname = f"{uuid.uuid4().hex}.mp3"
        fpath = os.path.join(AUDIO_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(mp3_bytes)

        if not BASE_URL:
            msg = f"✅ ทำเสียงเสร็จแล้ว แต่ยังไม่ได้ตั้ง BASE_URL จึงส่งลิงก์ไม่ได้ (ไฟล์ชื่อ {fname})"
        else:
            dl_url = f"{BASE_URL}/audio/{fname}"
            msg = f"✅ ทำเสียงเสร็จแล้ว 🎧\nดาวน์โหลดไฟล์ MP3: {dl_url}"

        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ ทำเสียงไม่สำเร็จ: {e}"))


# =========================
# ===== LINE Message =======
# =========================

def _get_user_id(event):
    return getattr(event.source, "user_id", None)

def _help_text():
    return (
        "📌 คำสั่งที่ใช้ได้\n"
        "1) ดับไฟ  → ส่งข้อความประกาศดับไฟ\n"
        "2) เสียง <ข้อความ> → สร้างไฟล์เสียง MP3\n"
        "3) /voices → ดูรายการเสียง 10 รายการแรก\n"
        "4) /setvoice <voice_id> → ตั้งเสียงที่จะใช้\n"
        "5) /voice → ดูว่าใช้เสียงอะไรอยู่\n"
        "6) /help → ดูคำสั่ง\n\n"
        "ตัวอย่าง:\n"
        "เสียง สวัสดีครับ ทดสอบระบบประกาศดับไฟ\n"
        "/setvoice English_CalmWoman"
    )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = (event.message.text or "").strip()
    user_id = _get_user_id(event)

    # ---------- HELP ----------
    if user_text.lower() in ["/help", "help"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=_help_text()))
        return

    # ---------- SHOW CURRENT VOICE ----------
    if user_text.lower() == "/voice":
        current = USER_VOICE.get(user_id) if user_id else None
        if not current:
            current = "English_CalmWoman"  # default
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🔊 voice_id ปัจจุบัน: {current}"))
        return

    # ---------- SET VOICE ----------
    if user_text.lower().startswith("/setvoice"):
        parts = user_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ใช้แบบนี้: /setvoice <voice_id>"))
            return

        vid = parts[1].strip()
        if user_id:
            USER_VOICE[user_id] = vid
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ ตั้งค่า VOICE_ID แล้ว: {vid}"))
        return

    # ---------- LIST VOICES ----------
    if user_text.lower() == "/voices":
        try:
            data = minimax_get_voice_list()

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
                name = v.get("name") or v.get("voice_name") or v.get("title") or "-"
                lines.append(f"{i}. {name}\nvoice_id: {vid}")

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="รายการเสียง (10 รายการแรก):\n" + "\n".join(lines))
            )
            return

        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ดึงรายการเสียงไม่สำเร็จ: {e}"))
            return

    # ---------- OUTAGE TEXT ----------
    if user_text == "ดับไฟ":
        today = thai_date(datetime.now())
        reply = build_outage_template(today)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # ---------- TTS ----------
    if user_text.startswith("เสียง"):
        text = user_text.replace("เสียง", "", 1).strip()
        if not text:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="พิมพ์แบบนี้ครับ: เสียง สวัสดีครับ ...")
            )
            return

        # ใช้เสียงที่ผู้ใช้ตั้งไว้ ถ้าไม่มีใช้ default
        voice_id = USER_VOICE.get(user_id) if user_id else None
        if not voice_id:
            voice_id = "English_CalmWoman"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⏳ กำลังสร้างเสียงด้วย MiniMax... เดี๋ยวส่งลิงก์ไฟล์ MP3 ให้ครับ")
        )

        if user_id:
            threading.Thread(
                target=tts_background_job,
                args=(user_id, text, voice_id),
                daemon=True
            ).start()
        return

    # ---------- DEFAULT ----------
    # ไม่ตอบอะไร (กันรบกวน)
    return


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
