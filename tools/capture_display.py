#!/usr/bin/env python3
"""Capture the ESP32 display state via UART and render it as HTML.

Requirements: pyserial>=3.5  (pip install pyserial)

Usage:
    python tools/capture_display.py --port COM13
    python tools/capture_display.py --port COM13 --output snapshot.html --baud 115200

The script sends '!SNAP\\n' to the device, waits for a JSON blob between
>>SNAP_START / >>SNAP_END markers, and writes an HTML mockup of the
240x135 display to the output file (default: snapshot.html in CWD).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial>=3.5")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Serial comms
# ---------------------------------------------------------------------------

def trigger_snapshot(port: str, baud: int = 115200, timeout_s: int = 15) -> dict | None:
    """Send !SNAP to the device and return the parsed JSON, or None on failure."""
    print(f"[capture] Opening {port} at {baud} baud …")
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except serial.SerialException as exc:
        print(f"[capture] ERROR: {exc}")
        return None

    with ser:
        # Flush stale input
        ser.reset_input_buffer()
        time.sleep(0.1)

        # Send trigger
        ser.write(b"!SNAP\r\n")
        ser.flush()
        print("[capture] Trigger sent — waiting for snapshot …")

        lines: list[str] = []
        started = False
        deadline = time.time() + timeout_s

        while time.time() < deadline:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()

            if line == ">>SNAP_START":
                started = True
                lines = []
            elif line == ">>SNAP_END" and started:
                break
            elif line.startswith(">>SNAP_ERR"):
                print(f"[capture] Device error: {line}")
                return None
            elif started:
                lines.append(line)

        if not started or not lines:
            print("[capture] No snapshot received (timeout or device not running uart_capture_task).")
            return None

        raw_json = "".join(lines)
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError as exc:
            print(f"[capture] JSON parse error: {exc}")
            print(f"[capture] Raw: {raw_json[:200]}")
            return None


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def _pct_bar(value: float | None, label: str, colour: str = "#4af") -> str:
    if value is None:
        return f'<div class="metric"><span>{label}</span><span class="val">N/A</span></div>'
    pct = min(max(float(value), 0), 100)
    return (
        f'<div class="metric">'
        f'<span>{label}</span>'
        f'<div class="bar"><div class="fill" style="width:{pct:.0f}%;background:{colour}"></div></div>'
        f'<span class="val">{pct:.0f}%</span>'
        f"</div>"
    )


def render_html(data: dict, output_path: Path) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    page = data.get("page", 0)
    wifi = "ONLINE" if data.get("wifi_online") else "OFFLINE"
    wifi_col = "#4f4" if data.get("wifi_online") else "#f44"
    weather = data.get("weather", {})
    metrics = data.get("metrics", {})
    esp = data.get("esp_status", {})
    solar = data.get("solar", {})
    weather_error = data.get("weather_error", "")
    net_streak = data.get("net_error_streak", 0)

    page_names = {0: "Weather", 1: "Tomorrow", 2: "5-Day", 3: "Solar", 4: "PC Metrics", 5: "ESP Status"}

    # Weather section
    if weather.get("valid"):
        weather_html = (
            f'<div class="big">{weather.get("temp_c", 0):.1f}°C</div>'
            f'<div>{weather.get("condition", "---")} &nbsp; Feels {weather.get("feels_like_c", 0):.1f}°C &nbsp; Hum {weather.get("humidity", 0)}%</div>'
            f'<div>Wind {weather.get("wind_ms", 0):.1f} m/s</div>'
        )
    else:
        err = weather_error or "(no data)"
        weather_html = f'<div class="err">OWM error: {err}</div>'

    # Forecast section
    forecast = data.get("forecast", [])
    fc_rows = ""
    for day in forecast[:5]:
        fc_rows += (
            f'<tr><td>{day.get("day","?")}</td>'
            f'<td>{day.get("condition","---")}</td>'
            f'<td>{day.get("temp_min",0):.0f}°/{day.get("temp_max",0):.0f}°</td>'
            f'<td>{day.get("humidity",0)}%</td></tr>'
        )
    forecast_html = (
        '<table><tr><th>Day</th><th>Cond.</th><th>Min/Max</th><th>Hum</th></tr>'
        + fc_rows + "</table>"
        if fc_rows else "<div class='err'>No forecast data</div>"
    )

    # PC metrics section
    pc_html = ""
    if metrics.get("valid"):
        pc_html = (
            _pct_bar(metrics.get("cpu_pct"), "CPU", "#4af")
            + _pct_bar(metrics.get("ram_pct"), "RAM", "#fa4")
            + _pct_bar(metrics.get("disk_pct"), "Disk", "#a4f")
            + _pct_bar(metrics.get("gpu_pct"), "GPU", "#4fa")
        )
        if metrics.get("temp_c") is not None:
            pc_html += f'<div class="metric"><span>CPU temp</span><span class="val">{metrics["temp_c"]:.1f}°C</span></div>'
    else:
        pc_html = '<div class="err">PC metrics not available</div>'

    # ESP status section
    esp_html = ""
    if esp:
        esp_html = (
            f'<div>RAM free: {esp.get("ram_free_kb", 0)} kB &nbsp;'
            f'CPU: {esp.get("cpu_mhz", 0)} MHz &nbsp;'
            f'RSSI: {esp.get("rssi", "?")} dBm</div>'
            f'<div>IP: {esp.get("ip", "?")} &nbsp; Uptime: {esp.get("uptime_s", 0)} s</div>'
        )
    else:
        esp_html = '<div class="err">ESP status not available</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ESP32 Display Snapshot — {ts}</title>
<style>
  body {{ font-family: monospace; background: #111; color: #eee; margin: 20px; }}
  h1 {{ color: #4af; font-size: 1.1em; }}
  .display {{ border: 2px solid #4af; border-radius: 6px; padding: 12px;
              width: 480px; background: #000; }}
  .status {{ font-size: 0.85em; color: {wifi_col}; margin-bottom: 8px; }}
  .page-tab {{ background: #222; padding: 4px 10px; border-radius: 4px;
               display: inline-block; margin-bottom: 8px; color: #4af; }}
  .big {{ font-size: 2.5em; font-weight: bold; color: #4af; }}
  .err {{ color: #f66; }}
  .section {{ margin-top: 12px; border-top: 1px solid #333; padding-top: 8px; }}
  .section h2 {{ font-size: 0.9em; color: #aaa; margin: 0 0 6px 0; }}
  table {{ border-collapse: collapse; font-size: 0.85em; }}
  td, th {{ padding: 3px 8px; border: 1px solid #333; }}
  th {{ color: #4af; }}
  .metric {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: 0.85em; }}
  .metric span:first-child {{ width: 60px; }}
  .bar {{ flex: 1; height: 10px; background: #333; border-radius: 3px; overflow: hidden; }}
  .fill {{ height: 100%; border-radius: 3px; }}
  .val {{ width: 40px; text-align: right; }}
  .meta {{ font-size: 0.8em; color: #666; margin-top: 16px; }}
</style>
</head>
<body>
<h1>ESP32 Display Snapshot</h1>
<div class="display">
  <div class="status">WiFi: {wifi} &nbsp;|&nbsp; Net errors: {net_streak} &nbsp;|&nbsp; Time synced: {'Yes' if data.get('time_synced') else 'No'}</div>
  <div class="page-tab">Active page: {page} — {page_names.get(page, "?")}</div>

  <div class="section">
    <h2>Page 0 — Current Weather</h2>
    {weather_html}
  </div>

  <div class="section">
    <h2>Page 2 — Forecast</h2>
    {forecast_html}
  </div>

  <div class="section">
    <h2>Page 4 — PC Metrics</h2>
    {pc_html}
  </div>

  <div class="section">
    <h2>Page 3 — Solar</h2>
    {'<div>Gen %.0fW &nbsp; Grid %.0fW &nbsp; Bat %.0f%%</div>' % (solar.get('generation_w',0), solar.get('grid_w',0), solar.get('battery_soc',0)) if solar.get('valid') else '<div class="err">Solar disabled or no data</div>'}
  </div>

  <div class="section">
    <h2>Page 5 — ESP Status</h2>
    {esp_html}
  </div>
</div>
<div class="meta">Snapshot taken: {ts} | Raw JSON fields: {len(data)}</div>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"[capture] Snapshot saved → {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="Serial port (e.g. COM13 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--output", default="snapshot.html", help="Output HTML file (default: snapshot.html)")
    parser.add_argument("--timeout", type=int, default=15, help="Seconds to wait for snapshot (default: 15)")
    args = parser.parse_args()

    data = trigger_snapshot(args.port, baud=args.baud, timeout_s=args.timeout)
    if data is None:
        sys.exit(1)

    print(f"[capture] Page: {data.get('page')}  WiFi: {data.get('wifi_online')}  "
          f"Weather valid: {data.get('weather', {}).get('valid')}")

    output_path = Path(args.output)
    render_html(data, output_path)


if __name__ == "__main__":
    main()
