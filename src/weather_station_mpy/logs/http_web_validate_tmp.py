import re
from pathlib import Path
import requests

log = Path(r"src/weather_station_mpy/logs/uart_web_rawsocket_validate_20260426_174820.txt")
text = log.read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()

ip = None
for ln in lines:
    m = re.search(r"\[WiFi\] Connected, IP:\s*([0-9\.]+)", ln)
    if m:
        ip = m.group(1)

web_ui = [ln for ln in lines if ("[WEB] Config UI:" in ln or ("[WEB]" in ln and "listen" in ln.lower()))]
tracebacks = [ln for ln in lines if "Traceback" in ln]
memerrs = [ln for ln in lines if "MemoryError" in ln]
binderrs = [ln for ln in lines if ("[WEB]" in ln and "bind" in ln.lower() and "error" in ln.lower()) or "bind/listen error" in ln.lower() or "socket bind error" in ln.lower()]

print(f"IP={ip or ''}")
print("WEB_UI_LINES=" + (" || ".join(web_ui) if web_ui else ""))
print(f"TRACEBACK_COUNT={len(tracebacks)}")
print(f"MEMORYERROR_COUNT={len(memerrs)}")
print(f"BIND_ERROR_COUNT={len(binderrs)}")

if ip:
    base = f"http://{ip}"
    try:
        r = requests.get(base + "/", timeout=8)
        body = r.text or ""
        ok_theme = bool(re.search(r"name=['\"]theme['\"]", body))
        ok_page = bool(re.search(r"name=['\"]page['\"]", body))
        ok_pc = bool(re.search(r"name=['\"]pc_url['\"]", body))
        print(f"GET_STATUS={r.status_code}")
        print(f"GET_FIELD_theme={ok_theme}")
        print(f"GET_FIELD_page={ok_page}")
        print(f"GET_FIELD_pc_url={ok_pc}")
    except Exception as e:
        print("GET_ERROR=" + str(e))

    body = "theme=light&page=0&page=2&page=4&pc_url=http%3A%2F%2F192.168.1.22%3A8765%2Fapi%2Fsystem%2Fmetrics"
    try:
        r2 = requests.post(base + "/save", data=body, headers={"Content-Type":"application/x-www-form-urlencoded"}, timeout=8)
        print(f"POST_STATUS={r2.status_code}")
    except Exception as e:
        print("POST_ERROR=" + str(e))
