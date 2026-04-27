import serial
import time

port = "COM13"
outfile = r"c:\WS\esp32_ideaspark_display\src\weather_station_mpy\logs\uart_post_gc_webbind.txt"

# Hard reset via DTR/RTS
with serial.Serial(port, 115200, timeout=1) as sp:
    sp.dtr = False
    sp.rts = True
    time.sleep(0.1)
    sp.rts = False
    time.sleep(0.1)
    print("[RESET] Hard reset issued via RTS")

time.sleep(1)

# Capture 120s
print("[CAPTURE] Starting 120s UART capture...")
lines = []
start = time.time()
with serial.Serial(port, 115200, timeout=0.5) as sp:
    while time.time() - start < 120:
        raw = sp.readline()
        if raw:
            try:
                line = raw.decode("utf-8", errors="replace").rstrip()
            except Exception:
                line = repr(raw)
            lines.append(line)
            print(line)

with open(outfile, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"\n[DONE] {len(lines)} lines captured -> {outfile}")
