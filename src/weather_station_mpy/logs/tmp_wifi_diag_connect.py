try:
    import ujson as json
except ImportError:
    import json

import time
import network


def load_cfg(path="config.json"):
    with open(path, "r") as handle:
        return json.load(handle)


def auth_name(mode):
    names = {
        0: "OPEN",
        1: "WEP",
        2: "WPA-PSK",
        3: "WPA2-PSK",
        4: "WPA/WPA2-PSK",
        5: "WPA2-ENTERPRISE",
        6: "WPA3-PSK",
        7: "WPA2/WPA3-PSK",
    }
    return names.get(mode, str(mode))


cfg = load_cfg()
ssid = cfg.get("wifi", {}).get("ssid", "")
password = cfg.get("wifi", {}).get("password", "")

print("[DIAG] ssid=%s pass_len=%d" % (ssid, len(password)))

wlan = network.WLAN(network.STA_IF)
try:
    wlan.active(False)
except Exception:
    pass

time.sleep_ms(1000)
wlan.active(True)
time.sleep_ms(300)

print("[DIAG] scan start")
try:
    found = False
    for entry in wlan.scan():
        essid = entry[0]
        bssid = entry[1]
        channel = entry[2]
        rssi = entry[3]
        auth = entry[4]
        hidden = entry[5]

        if isinstance(essid, bytes):
            essid_text = essid.decode("utf-8", "ignore")
        else:
            essid_text = str(essid)

        if essid_text == ssid:
            found = True
            bssid_hex = ":".join(["%02x" % x for x in bssid])
            print(
                "[DIAG] match ssid=%s bssid=%s ch=%d rssi=%d auth=%s hidden=%d"
                % (essid_text, bssid_hex, channel, rssi, auth_name(auth), hidden)
            )

    if not found:
        print("[DIAG] target SSID not found in scan")
except Exception as exc:
    print("[DIAG] scan error: %s" % exc)

try:
    wlan.disconnect()
except Exception:
    pass

print("[DIAG] connecting")
wlan.connect(ssid, password)

last = None
for _ in range(80):
    st = wlan.status()
    if st != last:
        print("[DIAG] status=%s" % st)
        last = st
    if wlan.isconnected():
        break
    time.sleep_ms(250)

if wlan.isconnected():
    print("[DIAG] connected ifconfig=%s" % (wlan.ifconfig(),))
else:
    print("[DIAG] not connected final_status=%s" % wlan.status())
