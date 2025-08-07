from mfrc522 import SimpleMFRC522
import RPi.GPIO as GPIO

GPIO.setwarnings(False)  # 关闭 GPIO 警告
reader = SimpleMFRC522()

try:
    print("请将RFID标签靠近...")
    id = reader.read_id()
    print(f"ID: {id}")

    reader.reader.select_tag(id)  # 选择标签
    status = reader.reader.authenticate(0x60, 4, reader.KEY, id)  # 尝试读取 Block 4
    if status == reader.reader.OK:
        data = reader.reader.read(4)
        print(f"Data in block 4: {data}")
    else:
        print("❌ 无法读取标签内容（可能被锁住）")
finally:
    GPIO.cleanup()
