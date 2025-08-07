import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
import mfrc522  # 💡 注意这个，才有底层 reader

reader_simple = SimpleMFRC522()
reader = mfrc522.MFRC522()  # ✅ 这样写才对

try:
    print("📡 请将 RFID 标签靠近...")
    id = reader_simple.read_id()
    print("🔍 UID:", id)

    # 尝试读取 block 4
    key = [0xFF] * 6  # 默认密码
    (status, uid) = reader.MFRC522_Anticoll()
    if status == reader.MI_OK:
        reader.MFRC522_SelectTag(uid)
        auth = reader.MFRC522_Auth(reader.PICC_AUTHENT1A, 4, key, uid)
        if auth == reader.MI_OK:
            data = reader.MFRC522_Read(4)
            print("✅ Block 4 内容:", data)
            reader.MFRC522_StopCrypto1()
        else:
            print("❌ Block 4 认证失败")
finally:
    GPIO.cleanup()
