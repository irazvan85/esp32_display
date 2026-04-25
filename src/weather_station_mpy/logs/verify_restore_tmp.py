import serial, time

s = serial.Serial('COM13', 115200, timeout=1)
s.setDTR(False); s.setRTS(False)
for _ in range(5):
    s.write(b'\x03')
    time.sleep(0.15)
buf = b''
dl = time.time() + 8
while time.time() < dl:
    c = s.read(s.in_waiting or 1)
    if c:
        buf += c
        if b'>>> ' in buf:
            break
s.reset_input_buffer()
time.sleep(0.2)
s.write(b"f=open('/config.json');import json;c=json.load(f);f.close();print('metrics.enabled=',c['metrics']['enabled'])\r\n")
time.sleep(1.5)
out = b''
while s.in_waiting:
    out += s.read(s.in_waiting)
    time.sleep(0.05)
s.close()
print(out.decode('utf-8', 'replace'))
