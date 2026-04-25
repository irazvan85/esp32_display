"""Restore original config.json to device via serial REPL."""
import serial
import time
import pathlib

PORT = "COM13"
BAUD = 115200

backup = pathlib.Path(r"c:\WS\esp32_ideaspark_display\src\weather_station_mpy\logs\config_device_backup_before_metrics_test.json")
cfg_str = backup.read_text(encoding="utf-8").strip()

def wait_for_prompt(s, timeout=10.0):
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = s.read(s.in_waiting or 1)
        if chunk:
            buf += chunk
            if b">>> " in buf:
                return True
    return False

s = serial.Serial(PORT, BAUD, timeout=1)
s.setDTR(False); s.setRTS(False)
time.sleep(0.3)

print("[RESTORE] Sending Ctrl+C to interrupt app...")
s.reset_input_buffer()
for _ in range(5):
    s.write(b"\x03")
    time.sleep(0.15)

print("[RESTORE] Waiting for REPL prompt...")
if not wait_for_prompt(s, timeout=8.0):
    print("[RESTORE] WARNING: No REPL prompt, trying anyway...")
else:
    print("[RESTORE] REPL ready.")

s.reset_input_buffer()
time.sleep(0.2)

escaped = cfg_str.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
cmd = f"f=open('/config.json','w');f.write('{escaped}');f.close();print('RESTORED_OK')\r\n"
s.write(cmd.encode())
time.sleep(2.0)

out = b""
while s.in_waiting:
    out += s.read(s.in_waiting)
    time.sleep(0.1)

resp = out.decode("utf-8", "replace")
print(f"[RESTORE] Response: {repr(resp[:300])}")

if "RESTORED_OK" in resp:
    print("[RESTORE] Config restored successfully.")
else:
    print("[RESTORE] WARNING: RESTORED_OK not seen. Check device manually.")

s.close()
