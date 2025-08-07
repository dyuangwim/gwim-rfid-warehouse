from mfrc522 import MFRC522
import RPi.GPIO as GPIO

GPIO.setwarnings(False)
reader = MFRC522.MFRC522()

try:
    print("请将 RFID 标签靠近读取器...")

    while True:
        (status, TagType) = reader.MFRC522_Request(reader.PICC_REQIDL)

        if status == reader.MI_OK:
            print("✅ 标签被侦测到")

            (status, uid) = reader.MFRC522_Anticoll()
            if status == reader.MI_OK:
                uid_str = ''.join(str(i) for i in uid)
                print("标签 ID:", uid_str)

                # 验证 sector 1 block 4
                block = 4
                reader.MFRC522_SelectTag(uid)
                key = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]  # 默认 key

                auth = reader.MFRC522_Auth(reader.PICC_AUTHENT1A, block, key, uid)
                if auth == reader.MI_OK:
                    data = reader.MFRC522_Read(block)
                    print("Block 4 内容:", data)
                    reader.MFRC522_StopCrypto1()
                else:
                    print("❌ 无法认证 Block 4")
                break
finally:
    GPIO.cleanup()
