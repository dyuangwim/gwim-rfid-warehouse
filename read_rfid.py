from mfrc522 import SimpleMFRC522

reader = SimpleMFRC522()

try:
    print("请将RFID标签靠近...")
    id, text = reader.read()
    print(f"ID: {id}")
    print(f"Data: {text}")
finally:
    pass
