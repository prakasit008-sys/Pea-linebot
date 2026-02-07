import os
from datetime import datetime

from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# ===== LINE TOKEN =====
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ===== ลิงก์ทำเสียง =====
TTS_LINK = "https://www.minimax.io/audio/text-to-speech"

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


# ===== หน้าเช็คเซิร์ฟเวอร์ =====
@app.route("/", methods=["GET"])
def home():
    return "OK", 200

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

# ===== รับข้อความ =====
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = (event.message.text or "").strip()

    # --- ทำเสียง AI ---
    if user_text == "AI":
        reply_text = (
            "🔊 ทำเสียง AI อัตโนมัติ\n"
            "กดลิงก์นี้ได้เลย:\n"
            f"{TTS_LINK}"
        )

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        return

    # --- ประกาศดับไฟ ---
    if user_text == "ดับไฟ":
        today = thai_date(datetime.now())
        reply_text = build_outage_template(today)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        return

    # --- อย่างอื่นไม่ตอบ ---
    return


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

