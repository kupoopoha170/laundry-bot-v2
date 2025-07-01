# ส่วนที่ 1: นำเข้าเครื่องมือและกุญแจที่จำเป็น
import os
import time
import threading
import tinytuya
from flask import Flask, request, abort
from line_bot_sdk import LineBotApi, WebhookHandler
from line_bot_sdk.exceptions import InvalidSignatureError
from line_bot_sdk.models import MessageEvent, TextMessage, TextSendMessage

# --- ส่วนที่ 2: ตั้งค่าต่างๆ (สามารถปรับแก้ได้) ---
# ค่าพลังงาน (วัตต์) ที่จะถือว่าเครื่องซักผ้า "เริ่มทำงาน"
POWER_THRESHOLD_ON = 20  # WATT
# ค่าพลังงาน (วัตต์) ที่จะถือว่าเครื่องซักผ้า "หยุดทำงาน"
POWER_THRESHOLD_OFF = 10  # WATT
# หน่วงเวลา (วินาที) เพื่อเช็คให้แน่ใจว่าเครื่องหยุดทำงานแล้วจริงๆ (เผื่อช่วงที่เครื่องหยุดระหว่างรอบ)
DELAY_BEFORE_NOTIFY = 180 # 180 วินาที = 3 นาที
# ----------------------------------------------------

# ส่วนที่ 3: ตั้งค่าการเชื่อมต่อ
# ดึงค่ากุญแจต่างๆ จาก Secrets ที่เราตั้งไว้
line_bot_api = LineBotApi(os.environ['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['LINE_CHANNEL_SECRET'])
device_id = os.environ['TUYA_DEVICE_ID']
local_key = os.environ['TUYA_LOCAL_KEY']

# ส่วนที่ 4: สร้างตัวแปรเพื่อเก็บสถานะการทำงาน
user_id_to_notify = None
is_washing = False
last_power_drop_time = None
notification_sent = False

# ส่วนที่ 5: สร้าง Web Server ด้วย Flask
app = Flask(__name__)

# สร้างเส้นทางสำหรับให้ LINE ส่งข้อมูลมาหาเรา
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# กำหนดว่าจะทำอะไรเมื่อได้รับข้อความจากผู้ใช้
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    global user_id_to_notify, is_washing, notification_sent
    if event.message.text == '1':
        user_id_to_notify = event.source.user_id
        is_washing = False # รีเซ็ตสถานะทุกครั้งที่ผู้ใช้ใหม่ร้องขอ
        notification_sent = False
        reply_message = 'รับทราบค่ะ! เมื่อซักผ้าเสร็จแล้วจะรีบมาแจ้งนะคะ 🧺'
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_message))
        print(f"ได้รับคำร้องขอจาก User ID: {user_id_to_notify}")

# ส่วนที่ 6: ฟังก์ชันหลักสำหรับตรวจสอบเครื่องซักผ้า
def monitor_laundry():
    global user_id_to_notify, is_washing, last_power_drop_time, notification_sent

    print("เริ่มการตรวจสอบเครื่องซักผ้า...")
    # เชื่อมต่อกับปลั๊ก Tuya
    d = tinytuya.OutletDevice(device_id, '1.1.1.1', local_key) # ใส่ IP มั่วไปก่อน, library จะหาเอง
    d.set_version(3.3)

    while True:
        # ถ้ายังไม่มีคนร้องขอให้แจ้งเตือน ก็ให้รอ
        if not user_id_to_notify:
            time.sleep(5)
            continue
        
        try:
            # ดึงข้อมูลสถานะจากปลั๊ก (รวมค่าพลังงาน)
            data = d.status()
            current_power = data['dps']['19'] # DP '19' คือค่าพลังงาน (วัตต์)
            print(f"สถานะปัจจุบัน: กำลังซัก={is_washing}, ค่าไฟ={current_power}W, รอแจ้งเตือน={not notification_sent}")

            # ตรรกะการทำงาน
            # 1. ถ้าเครื่องยังไม่ทำงาน และค่าไฟสูงขึ้น -> เปลี่ยนสถานะเป็น "กำลังซัก"
            if not is_washing and current_power > POWER_THRESHOLD_ON:
                is_washing = True
                notification_sent = False # รีเซ็ตสถานะการส่ง
                print("สถานะเปลี่ยน -> กำลังซัก")

            # 2. ถ้าเครื่องกำลังซัก และค่าไฟลดลง -> เริ่มจับเวลาหน่วง
            elif is_washing and current_power < POWER_THRESHOLD_OFF:
                if last_power_drop_time is None:
                    last_power_drop_time = time.time()
                    print(f"ค่าไฟลดลง เริ่มจับเวลาหน่วง {DELAY_BEFORE_NOTIFY} วินาที...")
                
                # 3. เช็คว่าเวลาหน่วงครบหรือยัง
                if (time.time() - last_power_drop_time) > DELAY_BEFORE_NOTIFY:
                    if not notification_sent:
                        print("ซักเสร็จแล้ว! กำลังส่ง LINE...")
                        line_bot_api.push_message(user_id_to_notify, TextSendMessage(text='✅ เครื่องซักผ้าทำงานเสร็จแล้วค่ะ!'))
                        notification_sent = True
                        user_id_to_notify = None # ล้างค่าเพื่อรอคนถัดไป
                        is_washing = False
                        last_power_drop_time = None
            
            # 4. ถ้าค่าไฟกลับมาสูงอีกครั้งระหว่างที่กำลังหน่วงเวลา -> แสดงว่ายังซักไม่เสร็จ
            elif is_washing and current_power > POWER_THRESHOLD_ON:
                if last_power_drop_time is not None:
                    print("เครื่องกลับมาทำงานต่อ ยกเลิกการหน่วงเวลา")
                last_power_drop_time = None

        except Exception as e:
            print(f"เกิดข้อผิดพลาดในการเชื่อมต่อกับปลั๊ก: {e}")
        
        # หน่วงเวลาก่อนตรวจสอบครั้งถัดไป
        time.sleep(10) # ตรวจสอบทุก 10 วินาที

# ส่วนที่ 7: เริ่มการทำงานของระบบ
# เริ่ม thread ของการตรวจสอบเครื่องซักผ้า ให้ทำงานอยู่เบื้องหลัง
monitor_thread = threading.Thread(target=monitor_laundry)
monitor_thread.daemon = True
monitor_thread.start()

# เริ่ม Web Server
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
