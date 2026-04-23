"""Diagnostic: check STAT_IDLE constant and WiFi status on the device."""
import serial
import time

s = serial.Serial("COM13", 115200, timeout=1)
# Interrupt running app
s.write(b"\x03")
time.sleep(0.5)
s.write(b"\x03")
time.sleep(0.5)
# Drain buffer
if s.in_waiting:
    s.read(s.in_waiting)
time.sleep(0.2)
if s.in_waiting:
    s.read(s.in_waiting)

# Send REPL command
cmd = (
    b"import network as n; w=n.WLAN(n.STA_IF);"
    b"print('STAT_IDLE=',getattr(n,'STAT_IDLE','N/A'));"
    b"print('STAT_CONNECTING=',getattr(n,'STAT_CONNECTING','N/A'));"
    b"print('status()=',w.status());"
    b"print('active()=',w.active())\r\n"
)
s.write(cmd)
time.sleep(2)
raw = b""
deadline = time.time() + 3
while time.time() < deadline:
    chunk = s.read(s.in_waiting or 1)
    if chunk:
        raw += chunk
    else:
        time.sleep(0.1)
print(raw.decode("utf-8", errors="replace"))
s.close()
