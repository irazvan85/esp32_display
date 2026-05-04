import network
import time

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
try:
    wlan.config(reconnects=0)
except Exception as exc:
    print("[DIAG] reconnects cfg err", exc)
try:
    pm_perf = getattr(network, "PM_PERFORMANCE", None)
    if pm_perf is not None:
        wlan.config(pm=pm_perf)
except Exception as exc:
    print("[DIAG] pm cfg err", exc)

try:
    wlan.disconnect()
except Exception:
    pass

time.sleep_ms(300)
print("[DIAG] tuned connect start")
wlan.connect("IOT", "Zaq!12345")
start = time.ticks_ms()
last = None
while time.ticks_diff(time.ticks_ms(), start) < 15000 and not wlan.isconnected():
    try:
        status = wlan.status()
    except Exception:
        status = -999
    if status != last:
        print("[DIAG] status", status)
        last = status
    time.sleep_ms(250)

print("[DIAG] connected", wlan.isconnected())
try:
    print("[DIAG] ifconfig", wlan.ifconfig())
except Exception as exc:
    print("[DIAG] ifconfig error", exc)
