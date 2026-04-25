import serial
import time

port = "COM13"
log = "src/weather_station_mpy/logs/uart_app_clean_test.txt"
lines = []

with serial.Serial(port, 115200, timeout=1) as s:
    start = time.time()
    while time.time() - start < 120:
        line = s.readline()
        if line:
            text = line.decode("utf-8", errors="replace").rstrip()
            lines.append(text)
            if any(k in text for k in ["[WiFi]", "IP:", "Connected", "timeout", "status=", "[BOOT]"]):
                print(text)

with open(log, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("LOG_WRITTEN", log)
