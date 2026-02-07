import os
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

VERSION = "v3"  # <-- เปลี่ยนเลขตรงนี้ทุกครั้งที่แก้โค้ด เพื่อเช็คว่าโค้ดใหม่เข้าจริง

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
app = Flask(__name__)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def on_text_message(event):
    text = (event.message.text or "").strip()

    # เช็คว่าโค้ดใหม่เข้าจริงไหม
    if text.lower() in ["ver", "version", "เวอร์ชั่น"]:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"BOT VERSION = {VERSION}")
        )
        return

    # คำสั่งหลัก
    if text == "ไฟดับ":
        reply = (
            "📢 ประกาศการไฟฟ้าส่วนภูมิภาคจังหวัดประจวบคีรีขันธ์\n"
            "แจ้งดับกระแสไฟฟ้าเพื่อปฎิบัติงาน\n"
            "📅 วันพฤหัสบดีที่ 12 กุมภาพันธ์ 2569\n"
            "⏰ เวลา 08:30 - 17:00 น.\n"
            "📍 ดับตั้งแต่ คอตีนสะพานร.ร.บ้านหว้ากอมิตรภาพ ถึง ปากทางหว้าโทนถนนเพชรเกษม \n"
            "******************************************************\n"
            "📅 วันศุกร์ที่ 13 กุมภาพันธ์ 2569\n"
            "⏰ เวลา 08:30 - 17:00 น.\n"
            "📍 ดับตั้งแต่ ร้านไทยถาวรต้นเกตุยาวไปถึงSF6ไร่คล่องฝั่งขาขึ้นกรุงเทพ \n"
            "\n"
            " "
           
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # ข้อความอื่น
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text='พิมพ์ "ไฟดับ" เพื่อดูประกาศ หรือกดที่เมนู "สร้างเสียงAI" ทำเสียงAIประกาศ')
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)







