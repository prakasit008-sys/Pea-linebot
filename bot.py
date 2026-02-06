from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = "r1l17imGiTgC2yPacumh5xXN8onhFSR7V2NHnr73P7riAyZ28VcIJI0SoY5Iy02qS5+THUAdX+d0RR0ncCG92W1aPFSxDnGtcCO5877YLPOwzXZ+qwXtFPOXaY9vqf5VDpQSpoTp/nZGc/vl9eGb3QdB04t89/1O/w1cDnyilFU="
CHANNEL_SECRET = "9e09aa8e611cb1ffab566246286242da"

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return 'Invalid signature', 400

    return 'OK'

import re

def build_announce(d):
    # จัดข้อความประกาศให้อ่านชัด (พร้อมเอาไปแปะทำเสียง)
    return (
        "ประกาศจากการไฟฟ้าส่วนภูมิภาค\n"
        "ขอแจ้งงดจ่ายกระแสไฟฟ้าชั่วคราว เพื่อปรับปรุงระบบจำหน่ายไฟฟ้า\n\n"
        f"วันที่ {d.get('date','-')}\n"
        f"เวลา {d.get('time','-')}\n"
        f"พื้นที่ {d.get('area','-')}\n"
        f"สาเหตุ {d.get('reason','-')}\n\n"
        f"ขออภัยในความไม่สะดวก\n"
        f"สอบถามเพิ่มเติม โทร {d.get('phone','1129')}"
    )

def parse_fireout(text):
    # รองรับ 2 แบบ:
    # 1) แบบบรรทัด:
    # /ไฟดับ
    # วันที่=10 ก.พ. 2569
    # เวลา=09:00-12:00
    # พื้นที่=หมู่ 3 บ้านคลอง
    # สาเหตุ=ปรับปรุงระบบ
    # โทร=1129
    #
    # 2) แบบบรรทัดเดียว:
    # /5555555555555555


    if not text.strip().startswith("/ไฟดับ"):
        return None

    raw = text.replace("/ไฟดับ", "", 1).strip()

    # แปลง ; เป็นขึ้นบรรทัด เพื่อ parse ง่าย
    raw = raw.replace(";", "\n")

    d = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k in ["วันที่", "date"]:
                d["date"] = v
            elif k in ["เวลา", "time"]:
                d["time"] = v
            elif k in ["พื้นที่", "area"]:
                d["area"] = v
            elif k in ["สาเหตุ", "เหตุผล", "reason"]:
                d["reason"] = v
            elif k in ["โทร", "phone"]:
                d["phone"] = v
    return d

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()

    # ===== กรณีไฟดับ =====
    if "ดับไฟ" in text or "ไฟดับ" in text:
        reply = f"""📣 ประกาศการไฟฟ้าส่วนภูมิภาค

แจ้งดับกระแสไฟฟ้า
รายละเอียด:
{text}

ขออภัยในความไม่สะดวก
☎ โทร 1129"""
        
        return TextSendMessage(text=reply)

    # ===== กรณีไฟกลับ =====
    if "ไฟกลับ" in text:
        reply = f"""✅ ประกาศการไฟฟ้าส่วนภูมิภาค

ขณะนี้จ่ายกระแสไฟฟ้าได้ตามปกติ
รายละเอียด:
{text}

ขออภัยในความไม่สะดวก
☎ โทร 1129"""

        return TextSendMessage(text=reply)

    # ===== ถ้าไม่เข้าเงื่อนไข =====
    return TextSendMessage(
        text="พิมพ์ข้อความเช่น:\nดับไฟ พรุ่งนี้ 9-11 ตลาดหน้าอำเภอ"
    )



