# bot.py
import os
import re
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# =========================
# 1) Config จาก ENV
# =========================
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    # กันพังแบบเงียบ ๆ เวลา token/secret ไม่ถูกตั้งค่า
    print("ERROR: Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_SECRET in environment variables.")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

app = Flask(__name__)


# =========================
# 2) Helper: ทำวันที่/เวลาแบบไทยง่าย ๆ (ไม่บังคับ)
# =========================
def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def build_help() -> str:
    return (
        "วิธีใช้บอท PEA AI Voice\n"
        "พิมพ์ข้อความธรรมดาได้เลย เช่น:\n"
        "• ดับไฟ พรุ่งนี้ 09:00-11:00 บ้านหนอง... ซอย...\n"
        "• ไฟกลับ เวลา 14:20 ตลาด...\n"
        "• ขัดข้อง สายขาด หน้าโรงเรียน...\n\n"
        "คำสั่ง:\n"
        "• /help หรือ /menu = ดูวิธีใช้\n"
        "• /test = ข้อความตัวอย่าง\n"
    )


def build_template_outage(detail: str) -> str:
    return (
        "📣 ประกาศการไฟฟ้าส่วนภูมิภาค\n"
        "เรื่อง แจ้งดับกระแสไฟฟ้าเพื่อปฏิบัติงาน\n\n"
        f"รายละเอียด:\n{detail}\n\n"
        "ขออภัยในความไม่สะดวก\n"
        "☎ โทร 1129"
    )


def build_template_restore(detail: str) -> str:
    return (
        "✅ ประกาศการไฟฟ้าส่วนภูมิภาค\n"
        "เรื่อง แจ้งจ่ายกระแสไฟฟ้าคืน\n\n"
        f"รายละเอียด:\n{detail}\n\n"
        "ขออภัยในความไม่สะดวก\n"
        "☎ โทร 1129"
    )


def build_template_emergency(detail: str) -> str:
    return (
        "🚨 ประกาศการไฟฟ้าส่วนภูมิภาค\n"
        "เรื่อง แจ้งเหตุขัดข้องระบบไฟฟ้า\n\n"
        f"รายละเอียด:\n{detail}\n\n"
        "เจ้าหน้าที่กำลังเร่งดำเนินการแก้ไข\n"
        "ขออภัยในความไม่สะดวก\n"
        "☎ โทร 1129"
    )


# =========================
# 3) Core: ตรวจประเภทข้อความแล้วจัดประกาศ
# =========================
OUTAGE_KEYWORDS = ["ดับไฟ", "ไฟดับ", "ตัดไฟ", "งดจ่ายไฟ", "ดับกระแส"]
RESTORE_KEYWORDS = ["ไฟกลับ", "จ่ายไฟ", "จ่ายกระแส", "ไฟมาแล้ว", "คืนกระแส"]
EMERGENCY_KEYWORDS = ["ขัดข้อง", "เหตุขัดข้อง", "ฉุกเฉิน", "สายขาด", "หม้อแปลง", "ไฟตก", "ไฟกระพริบ"]


def classify_message(text: str) -> str:
    """
    return: 'outage' | 'restore' | 'emergency' | 'unknown'
    """
    t = text.lower()

    def hit(words):
        return any(w in t for w in words)

    # ให้ outage สำคัญสุด ถ้าพิมพ์มีคำว่า "ดับไฟ"
    if hit(OUTAGE_KEYWORDS):
        return "outage"

    # restore
    if hit(RESTORE_KEYWORDS):
        return "restore"

    # emergency
    if hit(EMERGENCY_KEYWORDS):
        return "emergency"

    return "unknown"


def format_announcement(user_text: str) -> str:
    detail = normalize_spaces(user_text)

    kind = classify_message(detail)

    if kind == "outage":
        return build_template_outage(detail)
    if kind == "restore":
        return build_template_restore(detail)
    if kind == "emergency":
        return build_template_emergency(detail)

    # unknown
    return (
        "พิมพ์ข้อความให้บอทช่วยจัดประกาศได้เลย เช่น:\n"
        "• ดับไฟ พรุ่งนี้ 09:00-11:00 บ้านหนอง...\n"
        "• ไฟกลับ เวลา 14:20 ตลาด...\n"
        "• ขัดข้อง สายขาด หน้าโรงเรียน...\n\n"
        "หรือพิมพ์ /help"
    )


# =========================
# 4) LINE Webhook Endpoint
# =========================
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# =========================
# 5) Event Handler
# =========================
@handler.add(MessageEvent, message=TextMessage)
def on_text_message(event: MessageEvent):
    text = (event.message.text or "").strip()

    # คำสั่งสั้น ๆ
    if text.lower() in ["/help", "/menu", "help", "เมนู", "วิธีใช้"]:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=build_help())
        )
        return

    if text.lower() in ["/test", "ทดสอบ", "ตัวอย่าง"]:
        demo = (
            "ลองพิมพ์แบบนี้:\n"
            "1) ดับไฟ พรุ่งนี้ 09:00-11:00 บ้านหนองขาม ซอย 3\n"
            "2) ไฟกลับ เวลา 14:20 ตลาดเทศบาล\n"
            "3) ขัดข้อง สายขาด หน้าโรงเรียนวัด...\n"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=demo))
        return

    # โหมดหลัก: จัดประกาศอัตโนมัติ
    reply_text = format_announcement(text)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# =========================
# 6) Run (สำหรับรัน local)
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
