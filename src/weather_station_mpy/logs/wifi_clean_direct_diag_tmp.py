import network
import time

wlan = network.WLAN(network.STA_IF)
print('pre-connect status:', wlan.status())
print('is-active:', wlan.active())
wlan.config(reconnects=0)
wlan.disconnect()
time.sleep_ms(300)
wlan.connect('IOT', 'Zaq!12345')
print('post-connect status:', wlan.status())
deadline = time.ticks_ms() + 25000
while not wlan.isconnected():
    if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
        print('TIMEOUT status:', wlan.status())
        break
    s = wlan.status()
    print('status:', s)
    time.sleep_ms(500)
if wlan.isconnected():
    print('CONNECTED IP:', wlan.ifconfig()[0])
else:
    print('FAILED status:', wlan.status())
