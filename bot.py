import os
import time
import uuid
import threading
import json  # ✅ LOCK: เพิ่ม
import re
import csv
import io
from datetime import datetime

import requests
from flask import Flask, request, abort, send_file, Response  # ✅ เพิ่ม Response

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, AudioSendMessage  # ✅ เพิ่ม

app = Flask(__name__)

# =======================
# ENV
# =======================
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # เช่น https://pea-linebot.onrender.com
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

# =======================
# ✅ Admin / Limits (เพิ่มตามที่ขอ)
# =======================
# ใส่ userId ของแอดมิน คั่นด้วยคอมม่า เช่น "Uxxx,Uyyy"
ADMIN_USER_IDS = set([u.strip() for u in os.getenv("ADMIN_USER_IDS", "").split(",") if u.strip()])

# จำกัดความยาวข้อความ TTS
MAX_TTS_CHARS = int(os.getenv("MAX_TTS_CHARS", "1200"))

# อายุไฟล์เสียงที่เก็บไว้ (วินาที) ค่าเริ่มต้น 6 ชั่วโมง
AUDIO_MAX_AGE_SEC = int(os.getenv("AUDIO_MAX_AGE_SEC", str(6 * 3600)))

# =======================
# ✅ NEW: Google Sheet CSV (สำหรับคำสั่ง "ดับไฟ")
# =======================
# แนะนำให้ตั้งใน Render ENV: SHEET_CSV_URL
# ถ้าไม่ตั้ง จะใช้ค่า default ตามลิงก์ของคุณ
SHEET_CSV_URL = (os.getenv(
    "SHEET_CSV_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vTdIw6eIvTIrqS1PHxG8HKOiAlF5DISu1MfA_Uq4-mD-mECnb-ojFfDMlbpTtr4GZSF8JGSHhJj1hhO/pub?gid=0&single=true&output=csv"
) or "").strip()


def is_admin(event) -> bool:
    """ถ้าไม่ตั้ง ADMIN_USER_IDS เลย -> อนุญาตทุกคน (กันล็อคตัวเองตอนเริ่ม)"""
    uid = getattr(event.source, "user_id", "") or ""
    if not ADMIN_USER_IDS:Uf8d1dd32d0238a0f7874f98b86e3e75c
        return True
    return uid in ADMIN_USER_IDS


# ✅ เพิ่ม: กัน BASE_URL มีช่องว่าง/ขึ้นบรรทัดใหม่ ทำให้ LINE มองว่าไม่ใช่ https url
def _clean_base_url(url: str) -> str:
    u = (url or "").strip().replace("\r", "").replace("\n", "")
    return u.rstrip("/")


def build_https_url(base_url: str, path: str) -> str:
    b = _clean_base_url(base_url)
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    return b + p


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
# ✅ เพิ่ม: ลบไฟล์ mp3 เก่าอัตโนมัติ (กันดิสก์เต็ม)
# =======================
def cleanup_old_audio(max_age_sec: int = 6 * 3600):
    """ลบไฟล์ mp3 ที่เก่ากว่า max_age_sec"""
    try:
        now = time.time()
        for fn in os.listdir(AUDIO_DIR):
            if not fn.lower().endswith(".mp3"):
                continue
            fp = os.path.join(AUDIO_DIR, fn)
            if not os.path.isfile(fp):
                continue
            try:
                if now - os.path.getmtime(fp) > max_age_sec:
                    os.remove(fp)
            except Exception:
                pass
    except Exception:
        pass


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
# ✅ NEW: อ่าน Google Sheet CSV แล้วสร้างข้อความประกาศ
# =======================
def fetch_outages_from_sheet() -> list:
    """
    อ่าน CSV จาก Google Sheet ที่ publish แล้ว (SHEET_CSV_URL)
    คาดว่าหัวคอลัมน์: date, start, end, area, detail, status
    """
    if not SHEET_CSV_URL:
        return []

    r = requests.get(SHEET_CSV_URL, timeout=20)
    r.raise_for_status()

    # utf-8-sig กัน BOM
    text = r.content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    rows = []
    for row in reader:
        if not row:
            continue
        clean = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if clean.get("date"):  # กันแถวว่าง
            rows.append(clean)
    return rows


def build_outage_reply_from_sheet(rows: list) -> str:
    # กรองเฉพาะ status=active
    active = [r for r in rows if (r.get("status", "").strip().lower() == "active")]

    if not active:
        return "✅ ตอนนี้ไม่มีรายการดับไฟ (status=active) ใน Google Sheet"

    # เรียงตาม date แล้ว start
    active.sort(key=lambda r: (r.get("date", ""), r.get("start", "")))

    lines = ["📢 งานดับไฟแผนกปฏิบัติการ\n"]
    current_date = None

    for r in active:
        d = r.get("date", "")
        start = r.get("start", "")
        end = r.get("end", "")
        area = r.get("area", "")
        detail = r.get("detail", "")

        # คั่นวัน
        if d != current_date:
            if current_date is not None:
                lines.append("******************************")
            lines.append(f"📅 วันที่ {d}")
            current_date = d

        # รายละเอียดรายการ
        lines.append(f"⏰ เวลา {start} - {end} น.")
        if area:
            lines.append(f"📍 {area}")
        if detail:
            lines.append(f"{detail}")

    return "\n".join(lines).strip()


# =======================
# ✅ LOCK: Global voice lock (ทั้งบอท)
# =======================
# ✅ เปลี่ยนเล็กน้อย: ถ้ามี ENV MINIMAX_VOICE_ID ให้ใช้เป็นค่าเริ่มต้นก่อน (กันรีเซ็ต)
ENV_VOICE_ID = (os.getenv("MINIMAX_VOICE_ID") or "").strip()
DEFAULT_VOICE_ID = ENV_VOICE_ID if ENV_VOICE_ID else os.getenv(
    "DEFAULT_VOICE_ID", "moss_audio_8688355f-05ad-11f1-a527-12475c8c82b2"
)

# Render: ถ้ามี Persistent Disk แนะนำตั้ง ENV: SETTINGS_PATH=/var/data/pea_tts_settings.json
# ถ้ายังไม่มี disk ใช้ /tmp ได้ แต่ redeploy/restart อาจรีเซ็ตค่า
SETTINGS_PATH = os.getenv("SETTINGS_PATH", "/tmp/pea_tts_settings.json")
_settings_lock = threading.Lock()


def _load_settings() -> dict:
    with _settings_lock:
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # กันไฟล์พัง/ค่าว่าง
                    vid = (data.get("voice_id") or "").strip()
                    if vid:
                        return {"voice_id": vid}
            except Exception:
                pass
        return {"voice_id": DEFAULT_VOICE_ID}


def _save_settings(data: dict) -> None:
    with _settings_lock:
        parent = os.path.dirname(SETTINGS_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def get_voice_id() -> str:
    return _load_settings().get("voice_id", DEFAULT_VOICE_ID)


def set_voice_id(new_voice_id: str) -> None:
    data = _load_settings()
    data["voice_id"] = new_voice_id
    _save_settings(data)


# =======================
# Routes
# =======================
@app.route("/", methods=["GET"])
def home():
    return "OK", 200


@app.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    # ✅ เพิ่มเล็กน้อย: กัน path แปลกๆ
    filename = os.path.basename(filename)

    fpath = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(fpath):
        abort(404)

    # ✅ แก้: as_attachment=False เพื่อให้ LINE/Browser เล่นได้
    return send_file(
        fpath,
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name=filename
    )


# ✅ เพิ่ม: หน้าเล่นเสียงแบบวน (loop)
@app.route("/play/<path:filename>", methods=["GET"])
def play_audio_page(filename):
    # ป้องกัน path แปลกๆ
    filename = os.path.basename(filename)

    # ถ้าไฟล์ไม่มีอยู่ ให้ 404
    fpath = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(fpath):
        abort(404)

    html = f"""<!doctype html>
<html lang="th">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PEA Audio Loop</title>
  <style>
    body {{
      margin: 0;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #000;
      color: #fff;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    }}
    .box {{ text-align: center; padding: 24px; }}
    .title {{ font-size: 16px; opacity: .9; margin-bottom: 10px; }}
    audio {{ width: min(92vw, 520px); }}
    .hint {{ margin-top: 12px; font-size: 13px; opacity: .75; line-height: 1.4; }}
    .links {{ margin-top: 12px; font-size: 13px; opacity: .85; }}
    .links a {{ color: #7dd3fc; text-decoration: none; }}
    .links a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="box">
    <div class="title">🔁 เล่นวนอัตโนมัติ</div>
    <audio controls autoplay loop>
      <source src="/audio/{filename}" type="audio/mpeg" />
    </audio>
    <div class="hint">
      มือถือบางรุ่นจะไม่ให้เล่นอัตโนมัติ ต้องกด ▶️ 1 ครั้งก่อน<br/>
      หลังจากนั้นจะวนเองอัตโนมัติ
    </div>
    <div class="links">
      ดาวน์โหลดไฟล์: <a href="/audio/{filename}">/audio/{filename}</a>
    </div>
  </div>
</body>
</html>"""
    return Response(html, mimetype="text/html; charset=utf-8")


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
    return text.replace("\ufeff", "").replace("\u200b", "").strip()


def minimax_t2a_sync(text: str, voice_id: str) -> bytes:
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

    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0, "0"):
        raise RuntimeError(f"MiniMax error {base_resp.get('status_code')}: {base_resp.get('status_msg')}")

    audio_hex = (data.get("data") or {}).get("audio")
    if not audio_hex:
        raise RuntimeError(f"MiniMax did not return audio hex. Response: {str(data)[:600]}")

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
# Background job
# =======================
def tts_background_job(target_id: str, text: str, voice_id: str):
    try:
        # ✅ เพิ่ม: ลบไฟล์เก่า ป้องกันดิสก์เต็ม
        cleanup_old_audio(AUDIO_MAX_AGE_SEC)

        mp3_bytes = minimax_t2a_sync(text, voice_id=voice_id)

        fname = f"{uuid.uuid4().hex}.mp3"
        fpath = os.path.join(AUDIO_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(mp3_bytes)

        cleaned_base = _clean_base_url(BASE_URL)
        if not cleaned_base.startswith("https://"):
            msg = (
                "❌ ส่งเสียงใน LINE ไม่ได้ เพราะ BASE_URL ต้องเป็น https://...\n"
                "ไปตั้ง BASE_URL ใน Render ให้เป็นบรรทัดเดียว เช่น:\n"
                "https://pea-linebot.onrender.com"
            )
            line_bot_api.push_message(target_id, TextSendMessage(text=msg))
            return

        audio_url = build_https_url(cleaned_base, f"/audio/{fname}")
        play_url = build_https_url(cleaned_base, f"/play/{fname}")  # ✅ เพิ่ม: หน้า loop

        # ส่งเสียงเข้า LINE (เหมือนเดิม)
        line_bot_api.push_message(
            target_id,
            AudioSendMessage(
                original_content_url=audio_url,
                duration=30000
            )
        )

        # ✅ เพิ่ม: ลิงก์หน้าวนเสียงอัตโนมัติ
        line_bot_api.push_message(
            target_id,
            TextSendMessage(text=f"🔁 เปิดหน้าวนเล่นอัตโนมัติ: {play_url}")
        )

        # ลิงก์เดิมสำหรับดาวน์โหลด MP3
        line_bot_api.push_message(
            target_id,
            TextSendMessage(text=f"ดาวน์โหลดไฟล์ MP3: {audio_url}")
        )

    except Exception as e:
        line_bot_api.push_message(target_id, TextSendMessage(text=f"❌ ทำเสียงไม่สำเร็จ: {e}"))


# =======================
# Message handler
# =======================
def _help_text() -> str:
    return (
        "คำสั่งที่ใช้ได้:\n"
        "1) /help = ดูคำสั่ง\n"
        "2) /voices = ดูรายการเสียง (ตัวอย่าง)\n"
        "3) /setvoice <voice_id> = ตั้งเสียงที่ใช้ (ล็อคทั้งบอท) [แอดมิน]\n"
        "4) /myid = ดู userId ของตัวเอง\n"
        "5) เสียง <ข้อความ> = สร้างไฟล์ MP3\n"
        "6) ดับไฟ = ส่งประกาศดับไฟ\n\n"
        f"VOICE ปัจจุบัน: {get_voice_id()}\n"
        f"MAX_TTS_CHARS: {MAX_TTS_CHARS}\n"
        f"AUDIO_MAX_AGE_SEC: {AUDIO_MAX_AGE_SEC}"
    )


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = (event.message.text or "").strip()
    lower = user_text.lower()

    target_id = getattr(event.source, "group_id", None) \
        or getattr(event.source, "room_id", None) \
        or getattr(event.source, "user_id", None)

    # --- help ---
    if lower == "/help":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=_help_text()))
        return

    # ✅ เพิ่ม: ดู userId ของตัวเอง (เอาไว้ตั้ง ADMIN_USER_IDS)
    if lower == "/myid":
        uid = getattr(event.source, "user_id", "") or "unknown"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"Your userId:\n{uid}"))
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

    # --- setvoice (ล็อคทั้งบอท) ---
    if lower.startswith("/setvoice"):
        # ✅ เพิ่ม: จำกัดเฉพาะแอดมิน
        if not is_admin(event):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ คำสั่งนี้สำหรับแอดมินเท่านั้น"))
            return

        parts = user_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"วิธีใช้: /setvoice <voice_id>\nเสียงปัจจุบัน: {get_voice_id()}")
            )
            return

        new_voice = parts[1].strip()
        set_voice_id(new_voice)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"ตั้งค่า VOICE_ID (ล็อคทั้งบอท) แล้ว ✅\n{new_voice}")
        )
        return

    # --- outage ---
    if user_text == "ดับไฟ":
        # ✅ NEW: ดึงข้อมูลจาก Google Sheet CSV ก่อน (ถ้าพัง/ว่างค่อย fallback)
        try:
            rows = fetch_outages_from_sheet()
            msg = build_outage_reply_from_sheet(rows)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except Exception as e:
            # fallback ไป template เดิม (กันระบบล่ม)
            fallback = build_outage_template()
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"⚠️ อ่านชีตไม่สำเร็จ ใช้ข้อความสำรองแทน\nเหตุผล: {e}\n\n{fallback}")
            )
        return

    # --- tts ---
    if user_text.startswith("เสียง"):
        text = user_text.replace("เสียง", "", 1).strip()
        if not text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="พิมพ์แบบนี้ครับ: เสียง สวัสดีครับ ..."))
            return

        # ✅ เพิ่ม: จำกัดความยาวข้อความ
        if len(text) > MAX_TTS_CHARS:
            text = text[:MAX_TTS_CHARS].rstrip()
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"⚠️ ข้อความยาวเกินไป ตัดเหลือ {MAX_TTS_CHARS} ตัวอักษรแล้วกำลังทำเสียงให้ครับ")
            )
            # ไม่ return เพื่อให้ทำเสียงต่อได้

        voice_id = get_voice_id()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"⏳ กำลังสร้างเสียงด้วย MiniMax (Sync HTTP)...\nVOICE: {voice_id}\nเสร็จแล้วจะส่งเสียงให้ฟังใน LINE และลิงก์วนเล่น/ลิงก์โหลดครับ")
        )

        if not target_id:
            return

        threading.Thread(
            target=tts_background_job,
            args=(target_id, text, voice_id),
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
