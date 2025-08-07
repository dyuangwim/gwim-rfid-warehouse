from mfrc522 import MFRC522
import RPi.GPIO as GPIO

GPIO.setwarnings(False)
reader = MFRC522()

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

                block = 4  # 你要读取的区块编号
                key = [0xFF] * 6  # 默认 key
                reader.MFRC522_SelectTag(uid)
                auth = reader.MFRC522_Auth(reader.PICC_AUTHENT1A, block, key, uid)

                if auth == reader.MI_OK:
                    data = reader.MFRC522_Read(block)
                    print("✅ Block 4 内容:", data)
                    reader.MFRC522_StopCrypto1()
                else:
                    print("❌ 无法认证 block")
                break
finally:
    GPIO.cleanup()
