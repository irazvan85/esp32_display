"""
Controlled experiment: disable metrics, soft-reset, capture 180s UART.
Runs from PC via pyserial (not mpremote) to avoid raw-REPL blocking issues.
"""
import serial
import time
import pathlib
import sys

PORT = "COM13"
BAUD = 115200
LOG_PATH = pathlib.Path(r"c:\WS\esp32_ideaspark_display\src\weather_station_mpy\logs\uart_weather_metrics_disabled_180s.txt")
CAPTURE_SECS = 180

MODIFIED_CONFIG = """{
  "wifi": {
    "ssid": "IOT",
    "password": "Zaq!12345",
    "prefer_bssid_scan": false,
    "min_heap_for_scan_bytes": 98000,
    "bssid": "",
    "connect_timeout_ms": 20000,
    "check_interval_ms": 30000
  },
  "weather": {
    "enabled": true,
    "api_key": "ba8120071e9709b862c98ff82e97a5b5",
    "city": "Timisoara",
    "country": "RO",
    "refresh_ms": 600000,
    "stale_ms": 1800000
  },
  "time": {
    "ntp_server": "pool.ntp.org",
    "clock_refresh_ms": 1000,
    "tz_mode": "romania_eet_eest"
  },
  "solar": {
    "enabled": false,
    "app_id": "your_app_id",
    "app_secret": "your_app_secret",
    "email": "your@email.com",
    "pass_sha256": "sha256_hex_of_your_password",
    "station_id": 64729306,
    "refresh_ms": 300000
  },
  "metrics": {
    "enabled": false,
    "pc_url": "http://192.168.1.22:8765/api/system/metrics",
    "refresh_ms": 10000,
    "stale_ms": 120000,
    "timeout_ms": 3000
  },
  "display": {
    "backlight_on": true
  }
}"""

def send_repl_cmd(s, cmd, wait=0.5):
    s.write((cmd + "\r\n").encode())
    time.sleep(wait)
    out = b""
    while s.in_waiting:
        out += s.read(s.in_waiting)
        time.sleep(0.05)
    return out.decode("utf-8", "replace")

def write_config_to_device(s, config_str):
    print("[EXPERIMENT] Interrupting app...")
    s.write(b"\x03")
    time.sleep(0.3)
    s.write(b"\x03")
    time.sleep(0.5)
    # Discard any pending output
    s.reset_input_buffer()

    print("[EXPERIMENT] Writing config with metrics.enabled=false via REPL...")
    # Write the config in chunks to avoid REPL buffer issues
    lines = config_str.strip().replace('"', '\\"').replace("\n", "\\n")
    # Use a file-write approach via exec
    cmd = f'f=open("/config.json","w");f.write("{lines}");f.close();print("CFG_WRITTEN")'
    resp = send_repl_cmd(s, cmd, wait=2.0)
    print(f"[EXPERIMENT] Write response: {repr(resp[:200])}")
    if "CFG_WRITTEN" not in resp:
        print("[EXPERIMENT] WARNING: CFG_WRITTEN not found in response, trying alternative...")
        return False
    return True

def wait_for_prompt(s, timeout=10.0, prompt=b">>> "):
    """Wait until we see the REPL prompt in the serial stream."""
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = s.read(s.in_waiting or 1)
        if chunk:
            buf += chunk
            if prompt in buf:
                return True
    return False

def write_config_multiline(s, config_str):
    """Interrupt the running app, wait for REPL prompt, then write config."""
    print("[EXPERIMENT] Sending Ctrl+C to interrupt app...")
    s.reset_input_buffer()
    # Send several Ctrl+C to interrupt the running asyncio app
    for _ in range(5):
        s.write(b"\x03")
        time.sleep(0.15)

    print("[EXPERIMENT] Waiting for REPL prompt (>>> )...")
    got_prompt = wait_for_prompt(s, timeout=8.0)
    if not got_prompt:
        print("[EXPERIMENT] WARNING: No >>> prompt seen, trying anyway...")
    else:
        print("[EXPERIMENT] REPL prompt detected.")

    s.reset_input_buffer()
    time.sleep(0.2)

    cfg_oneliner = config_str.strip()
    escaped = cfg_oneliner.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

    print("[EXPERIMENT] Writing config file...")
    resp = send_repl_cmd(s, f"f=open('/config.json','w');f.write('{escaped}');f.close();print('CFG_OK')", wait=2.0)
    print(f"  write resp: {repr(resp[:200])}")
    return "CFG_OK" in resp

def soft_reset_and_capture(s):
    print("[EXPERIMENT] Soft-resetting device...")
    s.write(b"\x04")  # Ctrl+D = soft reset
    time.sleep(1.0)
    s.reset_input_buffer()

    print(f"[EXPERIMENT] Capturing UART for {CAPTURE_SECS}s...")
    lines = []
    deadline = time.time() + CAPTURE_SECS
    while time.time() < deadline:
        remaining = deadline - time.time()
        s.timeout = min(1.0, remaining + 0.1)
        raw = s.readline()
        if raw:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line:
                lines.append(line)
                elapsed = CAPTURE_SECS - (deadline - time.time())
                print(f"  [{elapsed:6.1f}s] {line}")
    return lines

def main():
    s = serial.Serial(PORT, BAUD, timeout=1)
    s.setDTR(False)
    s.setRTS(False)
    time.sleep(0.3)

    # Write modified config
    ok = write_config_multiline(s, MODIFIED_CONFIG)
    if not ok:
        print("[EXPERIMENT] FAILED to confirm config write. Aborting.")
        s.close()
        sys.exit(1)

    # Verify
    resp = send_repl_cmd(s, "f=open('/config.json');import json;c=json.load(f);f.close();print('metrics.enabled=',c['metrics']['enabled'])", wait=1.5)
    print(f"[EXPERIMENT] Verify: {resp.strip()}")

    # Capture
    lines = soft_reset_and_capture(s)
    s.close()

    LOG_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[EXPERIMENT] LOG_WRITTEN {LOG_PATH} lines={len(lines)}")

    # Analysis
    print("\n[EXPERIMENT] === ANALYSIS ===")
    wifi_ok = any("[WiFi] Connected" in l for l in lines)
    owm_ok = any("Hum:" in l or ("[OWM]" in l and "fetch error" not in l and "error" not in l.lower()) for l in lines)
    pc_errors = [l for l in lines if "[PC] fetch error" in l]
    pc_disabled = any("Disabled" in l and "PC" in l for l in lines)

    print(f"  WiFi connected: {wifi_ok}")
    print(f"  OWM success:    {owm_ok}")
    print(f"  [PC] errors:    {len(pc_errors)}")
    print(f"  [PC] disabled:  {pc_disabled}")

    if wifi_ok and owm_ok and len(pc_errors) == 0:
        print("\n[VERDICT] CONFIRMED: Disabling metrics allows weather fetch. Metrics polling was interfering.")
    elif wifi_ok and owm_ok:
        print("\n[VERDICT] PARTIAL: OWM succeeded but [PC] errors still present (task still running?)")
    elif wifi_ok and not owm_ok:
        print("\n[VERDICT] INCONCLUSIVE: WiFi ok but OWM still failing even with metrics disabled")
    else:
        print("\n[VERDICT] WiFi failed - cannot determine metrics impact")

if __name__ == "__main__":
    main()
