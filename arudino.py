import serial
import time

import config

SERIAL_PORT = config.SERIAL_PORT
BAUD_RATE = config.BAUD_RATE

arduino = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
time.sleep(2)  # Wait for the connection to establish

while True:
    if arduino.in_waiting > 0:
        data = arduino.readline().decode("utf-8", errors= 'ignore').strip()
        print(f"Received: {data}")