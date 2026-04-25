import network
import time
wlan = network.WLAN(network.STA_IF)
print('initial status:', wlan.status(), 'active:', wlan.active())
try:
    wlan.active(False)
except Exception as e:
    print('active(false) err:', e)
time.sleep_ms(500)
wlan.active(True)
try:
    wlan.config(reconnects=0)
except Exception as e:
    print('config err:', e)
try:
    wlan.disconnect()
except Exception as e:
    print('disconnect err:', e)
time.sleep_ms(500)
print('pre-connect status:', wlan.status(), 'active:', wlan.active())
try:
    wlan.connect('IOT', 'Zaq!12345')
except Exception as e:
    print('connect err:', e)
    raise
print('post-connect status:', wlan.status())
deadline = time.ticks_ms() + 30000
last = None
while not wlan.isconnected():
    s = wlan.status()
    if s != last:
        print('status:', s)
        last = s
    if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
        print('TIMEOUT status:', wlan.status())
        break
    time.sleep_ms(250)
if wlan.isconnected():
    print('CONNECTED IP:', wlan.ifconfig()[0])
else:
    print('FAILED status:', wlan.status())
