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
MINIMAX_GROUP_ID = os.getenv("MINIMAX_GROUP_ID", "")  # (เผื่อบางบัญชีต้องใช้) แต่ไม่บังคับ

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
# Default voice
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

def _minimax_headers():
    return {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }

def _clean_text_for_tts(text: str) -> str:
    # ลดโอกาสเจอ error เรื่องอักขระแปลก/invisible
    # (ไม่ลบ \n เพราะอยากให้เว้นวรรคอ่านเป็นช่วง ๆ ได้)
    text = text.replace("\ufeff", "").replace("\u200b", "").strip()
    return text

def _request_with_retry(method, url, *, headers=None, json=None, params=None, timeout=60, max_retry=5):
    """
    retry เมื่อ:
    - timeout
    - 5xx
    - base_resp.status_code == 1000/1001/1024/1033 (พวก server/unknown/timeout) ตามเอกสาร error code
    """
    backoff = 1.0
    last_err = None

    for attempt in range(1, max_retry + 1):
        try:
            r = requests.request(method, url, headers=headers, json=json, params=params, timeout=timeout)

            # retry on 5xx
            if 500 <= r.status_code <= 599:
                last_err = RuntimeError(f"MiniMax HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 10)
                continue

            r.raise_for_status()

            # ถ้าเป็น JSON และมี base_resp ให้เช็ค code
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype:
                data = r.json()
                base_resp = data.get("base_resp") or {}
                code = base_resp.get("status_code")
                msg = base_resp.get("status_msg")
                if code in (1000, 1001, 1024, 1033):
                    last_err = RuntimeError(f"MiniMax error {code}: {msg}")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 10)
                    continue
            return r

        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            time.sleep(backoff)
            backoff = min(backoff * 2, 10)
        except Exception as e:
            # ไม่รู้จะ retry ไหม: เก็บไว้แล้วหลุด
            last_err = e
            break

    raise RuntimeError(f"MiniMax request failed after retries: {last_err}")

def minimax_get_voice_list() -> dict:
    _require_minimax()
    url = "https://api.minimax.io/v1/get_voice"
    r = _request_with_retry("POST", url, headers=_minimax_headers(), json={"voice_type": "all"}, timeout=60)
    return r.json()

def minimax_create_task(text: str, voice_id: str) -> str:
    _require_minimax()

    url = "https://api.minimax.io/v1/t2a_async_v2"
    # บางบัญชีอาจต้องใส่ GroupId เป็น query (แต่เอกสารตัว create ไม่บังคับ)
    params = {"GroupId": MINIMAX_GROUP_ID} if MINIMAX_GROUP_ID else None

    payload = {
        "model": "speech-2.8-hd",
        "text": _clean_text_for_tts(text),
        "language_boost": "auto",
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1,
            "vol": 1,
            "pitch": 1
        },
        "audio_setting": {
            "audio_sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 2
        }
    }

    r = _request_with_retry("POST", url, headers=_minimax_headers(), json=payload, params=params, timeout=90)
    data = r.json()

    base_resp = data.get("base_resp") or {}
    code = base_resp.get("status_code", 0)
    if int(code) != 0:
        raise RuntimeError(f"MiniMax create error {code}: {base_resp.get('status_msg')}")

    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError(f"Cannot find task_id in response: {data}")

    return str(task_id)

def minimax_poll_success_and_get_file_id(task_id: str, timeout_sec: int = 240) -> str:
    _require_minimax()

    url = "https://api.minimax.io/v1/query/t2a_async_query_v2"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}"}

    start = time.time()
    while time.time() - start < timeout_sec:
        r = _request_with_retry("GET", url, headers=headers, params={"task_id": task_id}, timeout=60, max_retry=3)
        data = r.json()

        base_resp = data.get("base_resp") or {}
        code = base_resp.get("status_code", 0)
        if int(code) != 0:
            raise RuntimeError(f"MiniMax query error {code}: {base_resp.get('status_msg')}")

        status = (data.get("status") or "").lower()
        # doc: Processing/Success/Failed/Expired :contentReference[oaicite:3]{index=3}
        if status == "success":
            file_id = data.get("file_id")
            if not file_id:
                raise RuntimeError(f"Task success but missing file_id: {data}")
            return str(file_id)

        if status in ("failed", "expired"):
            raise RuntimeError(f"MiniMax task {status}: {data}")

        time.sleep(2)

    raise TimeoutError("MiniMax TTS timeout while waiting for Success")

def minimax_download_mp3(file_id: str) -> bytes:
    """
    วิธีที่เสถียร: เรียก /v1/files/retrieve เพื่อเอา download_url แล้วค่อยโหลด mp3
    (เอกสาร retrieve คืน file.download_url) :contentReference[oaicite:4]{index=4}
    """
    _require_minimax()

    # 1) Retrieve metadata (ได้ download_url)
    meta_url = "https://api.minimax.io/v1/files/retrieve"
    meta_headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}"}
    r = _request_with_retry("GET", meta_url, headers=meta_headers, params={"file_id": file_id}, timeout=60)
    meta = r.json()

    base_resp = meta.get("base_resp") or {}
    code = base_resp.get("status_code", 0)
    if int(code) != 0:
        raise RuntimeError(f"MiniMax retrieve error {code}: {base_resp.get('status_msg')}")

    file_obj = meta.get("file") or {}
    dl_url = file_obj.get("download_url")
    if not dl_url:
        raise RuntimeError(f"MiniMax retrieve missing download_url: {meta}")

    # 2) Download actual mp3
    r2 = _request_with_retry("GET", dl_url, headers=None, timeout=120)
    ctype2 = (r2.headers.get("Content-Type") or "").lower()

    # บางที CDN อาจไม่ส่ง content-type audio ชัด ๆ แต่ยังเป็น mp3 bytes ได้
    content = r2.content
    if len(content) < 1000:
        # ถ้าเล็กผิดปกติ ลองโชว์ preview กันพลาด
        preview = (r2.text[:200] if "text" in ctype2 or "json" in ctype2 else str(content[:200]))
        raise RuntimeError(f"Downloaded file too small / not audio? Content-Type={ctype2}, preview={preview}")

    return content

# =======================
# Background job
# =======================
def tts_background_job(target_id: str, text: str, voice_id: str):
    try:
        task_id = minimax_create_task(text, voice_id=voice_id)
        file_id = minimax_poll_success_and_get_file_id(task_id, timeout_sec=240)
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

        line_bot_api.push_message(target_id, TextSendMessage(text=msg))

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

    # หาว่าต้อง push กลับไปที่ไหน (user / group / room)
    target_id = getattr(event.source, "user_id", None) \
        or getattr(event.source,
