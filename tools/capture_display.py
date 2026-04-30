#!/usr/bin/env python3
"""Capture ESP32 display data over UART and render HTML reports.

Requirements: pyserial>=3.5  (pip install pyserial)

Usage:
    python tools/capture_display.py --port COM13
    python tools/capture_display.py --port COM13 --all-pages
    python tools/capture_display.py --port COM13 --all-pages --pages 0,1,2,3,4,5

The script uses `display_capture` when available; otherwise it derives visible
page fields from snapshot data using the same page rules used by the UI.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial>=3.5")
    sys.exit(1)


PAGE_NAMES = {
    0: "Weather",
    1: "Tomorrow",
    2: "5-Day",
    3: "Solar",
    4: "PC Metrics",
    5: "ESP Status",
}

_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _normalize_pages(raw_pages, fallback_page=0):
    pages = []
    if isinstance(raw_pages, list):
        for item in raw_pages:
            if isinstance(item, int) and 0 <= item <= 5 and item not in pages:
                pages.append(item)

    if not pages:
        if isinstance(fallback_page, int) and 0 <= fallback_page <= 5:
            return [fallback_page]
        return [0]

    return pages


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _page_label(page):
    labels = {
        0: "[1/6]",
        1: "[2/6]",
        2: "[3/6]",
        3: "[4/6]",
        4: "[5/6]",
        5: "[6/6]",
    }
    return labels.get(page, "[?/6]")


def _status_age_label(snapshot, snapshot_ms, last_fetch_ms=0, age_prefix="wx"):
    if snapshot_ms and last_fetch_ms:
        age_ms = snapshot_ms - last_fetch_ms
        if age_ms < 0:
            age_ms = 0
        age_s = age_ms // 1000
        if age_s < 60:
            return "%s %ds" % (age_prefix, age_s)
        return "%s %dm" % (age_prefix, age_s // 60)

    if not snapshot.get("wifi_online"):
        return "offline"
    if age_prefix == "pc":
        return "no pc"
    return "no wx"


def _format_uptime(total_seconds):
    total_seconds = _safe_int(total_seconds, 0)
    if total_seconds < 0:
        total_seconds = 0
    days = total_seconds // 86_400
    hours = (total_seconds % 86_400) // 3_600
    minutes = (total_seconds % 3_600) // 60
    if days > 0:
        return "%dd %02dh" % (days, hours)
    return "%02dh %02dm" % (hours, minutes)


def _derive_display_capture(snapshot):
    if not isinstance(snapshot, dict):
        return {}

    page = snapshot.get("page")
    if not isinstance(page, int):
        page = 0

    details = {}
    lines = []
    payload = {
        "source": "derived_from_app_state",
        "page": page,
        "page_name": PAGE_NAMES.get(page, "?"),
        "page_label": _page_label(page),
        "wifi_online": bool(snapshot.get("wifi_online")),
        "time_synced": bool(snapshot.get("time_synced")),
        "render_ms": _safe_int(snapshot.get("snapshot_ms"), 0),
        "visible_text": lines,
        "details": details,
    }

    weather = snapshot.get("weather", {}) if isinstance(snapshot.get("weather"), dict) else {}
    forecast = snapshot.get("forecast", []) if isinstance(snapshot.get("forecast"), list) else []
    solar = snapshot.get("solar", {}) if isinstance(snapshot.get("solar"), dict) else {}
    metrics = snapshot.get("metrics", {}) if isinstance(snapshot.get("metrics"), dict) else {}
    esp = snapshot.get("esp_status", {}) if isinstance(snapshot.get("esp_status"), dict) else {}

    snapshot_ms = _safe_int(snapshot.get("snapshot_ms"), 0)

    if page == 0:
        local_time = snapshot.get("local_time")
        if isinstance(local_time, list) and len(local_time) >= 7:
            clock_text = "%02d:%02d:%02d" % (
                _safe_int(local_time[3]),
                _safe_int(local_time[4]),
                _safe_int(local_time[5]),
            )
            day_idx = _safe_int(local_time[6], -1)
            day = _DAYS[day_idx] if 0 <= day_idx < 7 else "?"
            month_idx = _safe_int(local_time[1], 1) - 1
            month = _MONTHS[month_idx] if 0 <= month_idx < 12 else "?"
            date_text = "%s  %02d %s %04d" % (
                day,
                _safe_int(local_time[2]),
                month,
                _safe_int(local_time[0]),
            )
            lines.extend((clock_text, date_text))
            details["clock"] = clock_text
            details["date"] = date_text
        elif not snapshot.get("time_synced"):
            lines.append("Syncing...")
            details["clock"] = "Syncing..."

        if weather.get("valid"):
            temp_text = "%+.1fC" % _safe_float(weather.get("temp_c"), 0.0)
            feels_text = "F:%.1f H:%d%%" % (
                _safe_float(weather.get("feels_like_c"), 0.0),
                _safe_int(weather.get("humidity"), 0),
            )
            cond = str(weather.get("condition", "---"))
            max_chars = (240 - 4) // 8
            if len(cond) > max_chars:
                cond = cond[: max_chars - 1]

            lines.extend((temp_text, feels_text, cond))
            details["temp_text"] = temp_text
            details["feels_text"] = feels_text
            details["condition_text"] = cond

            wind = weather.get("wind_ms")
            if wind is not None:
                wind_text = "Wind:%.1fm/s" % _safe_float(wind, 0.0)
                lines.append(wind_text)
                details["wind_text"] = wind_text

            stale = False
            last_weather_fetch = _safe_int(snapshot.get("last_weather_fetch_ms"), 0)
            if last_weather_fetch and snapshot_ms:
                stale = (snapshot_ms - last_weather_fetch) > _safe_int(1_800_000)
            details["stale"] = bool(stale)
            if stale:
                lines.append("[stale]")
        else:
            if not snapshot.get("wifi_online"):
                msg = "No Network"
            elif not snapshot.get("time_synced"):
                msg = "Syncing..."
            elif snapshot.get("weather_error"):
                msg = "OWM err %s" % snapshot.get("weather_error")
                max_chars = (240 - 4) // 8
                if len(msg) > max_chars:
                    msg = msg[:max_chars]
            else:
                msg = "Fetching wx..."
            lines.append(msg)
            details["message"] = msg

        status_age = _status_age_label(
            snapshot,
            snapshot_ms,
            _safe_int(snapshot.get("last_weather_fetch_ms"), 0),
            "wx",
        )
        details["status_age_label"] = status_age
        lines.extend((status_age, _page_label(page)))

    elif page == 1:
        if len(forecast) < 2:
            lines.append("Forecast pending")
            details["message"] = "Forecast pending"
        else:
            fc = forecast[1]
            date_raw = str(fc.get("date", ""))
            day_text = str(fc.get("day", "?"))
            try:
                month_idx = int(date_raw[5:7]) - 1
                month_text = _MONTHS[month_idx] if 0 <= month_idx < 12 else "?"
                date_text = "%s  %s %s" % (day_text, date_raw[8:10], month_text)
            except (TypeError, ValueError, IndexError):
                date_text = "%s  %s" % (day_text, date_raw)

            hi_text = "Hi:%+.0fC" % _safe_float(fc.get("temp_max"), 0.0)
            lo_text = "Lo:%+.0fC" % _safe_float(fc.get("temp_min"), 0.0)
            cond_text = str(fc.get("condition", "---"))
            hum_text = "Humidity: %d%%" % _safe_int(fc.get("humidity"), 0)
            lines.extend((date_text, hi_text, lo_text, cond_text, hum_text))
            details["date_text"] = date_text

        status_age = _status_age_label(snapshot, 0, 0, "wx")
        details["status_age_label"] = status_age
        lines.extend((status_age, _page_label(page)))

    elif page == 2:
        if not forecast:
            lines.append("Forecast pending")
            details["message"] = "Forecast pending"
        else:
            rows = []
            for fc in forecast[:5]:
                row = "%s %+.0f/%+.0f %s" % (
                    str(fc.get("day", "?"))[:3],
                    _safe_float(fc.get("temp_min"), 0.0),
                    _safe_float(fc.get("temp_max"), 0.0),
                    str(fc.get("condition", "---"))[:10],
                )
                rows.append(row)
                lines.append(row)
            details["rows"] = rows

        status_age = _status_age_label(snapshot, 0, 0, "wx")
        details["status_age_label"] = status_age
        lines.extend((status_age, _page_label(page)))

    elif page == 3:
        if not solar.get("valid"):
            lines.append("Solar pending")
            details["message"] = "Solar pending"
        else:
            gen_text = "%.2fkW" % (_safe_float(solar.get("generation_w"), 0.0) / 1000.0)
            grid_text = "Grid: %.2fkW" % (_safe_float(solar.get("grid_w"), 0.0) / 1000.0)
            bat_text = "Battery: %.0f%%" % _safe_float(solar.get("battery_soc"), 0.0)
            lines.extend(("Generation:", gen_text, grid_text, bat_text))

        status_age = _status_age_label(snapshot, 0, 0, "wx")
        details["status_age_label"] = status_age
        lines.extend((status_age, _page_label(page)))

    elif page == 4:
        subpage = _safe_int(snapshot.get("metrics_subpage"), 0)
        details["subpage"] = subpage
        if not metrics.get("valid"):
            if not snapshot.get("wifi_online"):
                msg = "No Network"
            elif _safe_int(snapshot.get("last_metrics_fetch_ms"), 0):
                msg = "PC service offline"
            else:
                msg = "Waiting PC data..."
            lines.append(msg)
            details["message"] = msg
        else:
            temp_c = metrics.get("temp_c")
            temp_text = "N/A" if temp_c is None else "%.0fC" % _safe_float(temp_c, 0.0)

            tiles = []
            if subpage == 1:
                gpu_pct = metrics.get("gpu_pct")
                gpu_temp = metrics.get("gpu_temp_c")
                if gpu_pct is None and gpu_temp is None:
                    lines.append("No GPU data")
                    details["message"] = "No GPU data"
                else:
                    tiles = [
                        {"label": "GPU%", "value": "N/A" if gpu_pct is None else "%d%%" % _safe_int(gpu_pct, 0)},
                        {"label": "GPU_T", "value": "N/A" if gpu_temp is None else "%.0fC" % _safe_float(gpu_temp, 0.0)},
                        {"label": "CPU", "value": "%d%%" % _safe_int(metrics.get("cpu_pct"), 0)},
                        {"label": "CPU_T", "value": temp_text},
                    ]
            else:
                tiles = [
                    {"label": "CPU", "value": "%d%%" % _safe_int(metrics.get("cpu_pct"), 0)},
                    {"label": "RAM", "value": "%d%%" % _safe_int(metrics.get("ram_pct"), 0)},
                    {"label": "DSK", "value": "%d%%" % _safe_int(metrics.get("disk_pct"), 0)},
                    {"label": "TEMP", "value": temp_text},
                ]

            details["tiles"] = tiles
            for tile in tiles:
                lines.append("%s: %s" % (tile["label"], tile["value"]))

        uptime = _format_uptime(metrics.get("uptime_s", 0))
        status_age = _status_age_label(
            snapshot,
            snapshot_ms,
            _safe_int(snapshot.get("last_metrics_fetch_ms"), 0),
            "up:" + uptime,
        )
        details["uptime"] = uptime
        details["status_age_label"] = status_age
        lines.extend((status_age, _page_label(page)))

    elif page == 5:
        if not esp:
            lines.append("Collecting...")
            details["message"] = "Collecting..."
        else:
            ram_free = _safe_int(esp.get("ram_free_kb"), 0)
            ram_used = _safe_int(esp.get("ram_used_kb"), -1)
            if ram_used >= 0:
                ram_line = "%dkB free %dkB used" % (ram_free, ram_used)
            else:
                ram_line = "%dkB free" % ram_free
            cpu_line = "%dMHz Flash:%s" % (
                _safe_int(esp.get("cpu_mhz"), 0),
                "%dMB" % (_safe_int(esp.get("flash_kb"), 0) // 1024)
                if _safe_int(esp.get("flash_kb"), 0) >= 1024
                else "%dkB" % _safe_int(esp.get("flash_kb"), 0),
            )
            fs_line = "%dkB/%dkB free" % (
                _safe_int(esp.get("fs_free_kb"), 0),
                _safe_int(esp.get("fs_total_kb"), 0),
            )
            ip_line = str(esp.get("ip", "?"))
            if snapshot.get("wifi_online"):
                wifi_line = "%ddBm Ch:%d" % (_safe_int(esp.get("rssi"), 0), _safe_int(esp.get("channel"), 0))
            else:
                wifi_line = "offline"

            up_s = _safe_int(esp.get("uptime_s"), 0)
            up_d = up_s // 86_400
            up_h = (up_s % 86_400) // 3_600
            up_m = (up_s % 3_600) // 60
            if up_d > 0:
                up_line = "%dd %02dh %02dm" % (up_d, up_h, up_m)
            else:
                up_line = "%02dh %02dm" % (up_h, up_m)

            lines.extend(
                (
                    "RAM: " + ram_line,
                    "CPU: " + cpu_line,
                    "FS: " + fs_line,
                    "IP: " + ip_line,
                    "WiFi: " + wifi_line,
                    "Up: " + up_line,
                )
            )

        status_age = _status_age_label(snapshot, 0, 0, "wx")
        details["status_age_label"] = status_age
        lines.extend((status_age, _page_label(page)))

    return payload


def _extract_display_capture(snapshot):
    if not isinstance(snapshot, dict):
        return {}

    capture = snapshot.get("display_capture")
    if isinstance(capture, dict) and isinstance(capture.get("visible_text"), list):
        return capture

    return _derive_display_capture(snapshot)


def _observed_page(snapshot):
    capture = _extract_display_capture(snapshot)
    page = capture.get("page")
    if isinstance(page, int):
        source = capture.get("source")
        if source == "derived_from_app_state":
            return page, "derived"
        return page, "display_capture"

    if isinstance(snapshot, dict):
        page = snapshot.get("page")
        if isinstance(page, int):
            return page, "app_state"

    return None, "unknown"


def _readline_text(ser):
    raw = ser.readline()
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace").strip()


def _snapshot_from_open_serial(ser, timeout_s=15):
    """Send !SNAP and parse one JSON payload.

    Returns (snapshot_dict_or_none, error_string_or_empty).
    """
    ser.write(b"!SNAP\r\n")
    ser.flush()

    lines = []
    started = False
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        line = _readline_text(ser)
        if not line:
            continue

        if line == ">>SNAP_START":
            started = True
            lines = []
            continue

        if line == ">>SNAP_END" and started:
            try:
                payload = json.loads("".join(lines))
                return payload, ""
            except json.JSONDecodeError as exc:
                return None, "json decode error: %s" % exc

        if line.startswith(">>SNAP_ERR"):
            return None, line

        if started:
            lines.append(line)

    return None, "snapshot timeout"


def send_command(ser, command, timeout_s=5):
    """Send a UART control command and wait for >>CMD_OK / >>CMD_ERR."""
    wire = (command.strip() + "\r\n").encode("utf-8")
    ser.write(wire)
    ser.flush()

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = _readline_text(ser)
        if not line:
            continue
        if line.startswith(">>CMD_OK"):
            return True, line
        if line.startswith(">>CMD_ERR"):
            return False, line

    return False, "timeout waiting for command ack"


def trigger_snapshot(port, baud=115200, timeout_s=15):
    """Capture a single snapshot from the device, or return None on failure."""
    print("[capture] Opening %s at %d baud" % (port, baud))
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except serial.SerialException as exc:
        print("[capture] ERROR: %s" % exc)
        return None

    with ser:
        ser.reset_input_buffer()
        time.sleep(0.1)
        data, error = _snapshot_from_open_serial(ser, timeout_s=timeout_s)
        if data is None:
            print("[capture] ERROR: %s" % error)
            return None
        return data


def capture_page_sweep(port, baud=115200, timeout_s=15, settle_ms=300, page_override=None):
    """Capture snapshots for all target pages using !PAGE + !SNAP commands."""
    result = {
        "captures": [],
        "enabled_pages": [],
        "initial": None,
        "error": "",
    }

    print("[capture] Opening %s at %d baud" % (port, baud))
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except serial.SerialException as exc:
        result["error"] = "serial open failed: %s" % exc
        return result

    with ser:
        ser.reset_input_buffer()
        time.sleep(0.1)

        initial, init_error = _snapshot_from_open_serial(ser, timeout_s=timeout_s)
        if initial is None:
            result["error"] = "initial snapshot failed: %s" % init_error
            return result

        result["initial"] = initial
        enabled_pages = _normalize_pages(initial.get("enabled_pages"), initial.get("page", 0))
        result["enabled_pages"] = enabled_pages

        if page_override is None:
            target_pages = enabled_pages
        else:
            target_pages = _normalize_pages(page_override, initial.get("page", 0))

        for page in target_pages:
            ok, ack = send_command(ser, "!PAGE %d" % page, timeout_s=min(timeout_s, 5))
            if not ok:
                result["captures"].append(
                    {
                        "requested_page": page,
                        "observed_page": None,
                        "observed_source": "unknown",
                        "ok": False,
                        "error": ack,
                        "ack": ack,
                        "snapshot": None,
                    }
                )
                continue

            if settle_ms > 0:
                time.sleep(float(settle_ms) / 1000.0)

            snap, snap_error = _snapshot_from_open_serial(ser, timeout_s=timeout_s)
            if snap is None:
                result["captures"].append(
                    {
                        "requested_page": page,
                        "observed_page": None,
                        "observed_source": "unknown",
                        "ok": False,
                        "error": snap_error,
                        "ack": ack,
                        "snapshot": None,
                    }
                )
                continue

            observed, observed_source = _observed_page(snap)
            match = observed == page
            result["captures"].append(
                {
                    "requested_page": page,
                    "observed_page": observed,
                    "observed_source": observed_source,
                    "ok": bool(match),
                    "error": "" if match else "requested %s got %s" % (page, observed),
                    "ack": ack,
                    "snapshot": snap,
                }
            )

    return result


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def _pct_bar(value, label, colour="#4af"):
    if value is None:
        return '<div class="metric"><span>%s</span><span class="val">N/A</span></div>' % label
    pct = min(max(float(value), 0.0), 100.0)
    return (
        '<div class="metric">'
        '<span>%s</span>'
        '<div class="bar"><div class="fill" style="width:%.0f%%;background:%s"></div></div>'
        '<span class="val">%.0f%%</span>'
        "</div>"
    ) % (label, pct, colour, pct)


def render_html(data, output_path):
    """Render one snapshot as a human-readable HTML summary."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    display_capture = _extract_display_capture(data)

    page = data.get("page", 0)
    if isinstance(display_capture.get("page"), int):
        page = display_capture.get("page")

    wifi_online = bool(display_capture.get("wifi_online", data.get("wifi_online")))
    time_synced = bool(display_capture.get("time_synced", data.get("time_synced")))
    wifi = "ONLINE" if wifi_online else "OFFLINE"
    wifi_col = "#4f4" if wifi_online else "#f44"

    weather = data.get("weather", {})
    metrics = data.get("metrics", {})
    esp = data.get("esp_status", {})
    solar = data.get("solar", {})
    weather_error = data.get("weather_error", "")
    net_streak = data.get("net_error_streak", 0)

    render_lines = display_capture.get("visible_text") if isinstance(display_capture, dict) else []
    if isinstance(render_lines, list) and render_lines:
        render_lines_html = "<ol class='render-lines'>%s</ol>" % "".join(
            "<li>%s</li>" % html.escape(str(line)) for line in render_lines
        )
    else:
        render_lines_html = "<div class='err'>No renderer payload found in snapshot.</div>"

    render_details = display_capture.get("details") if isinstance(display_capture, dict) else {}
    if isinstance(render_details, dict) and render_details:
        render_details_html = "<pre>%s</pre>" % html.escape(json.dumps(render_details, indent=2, sort_keys=True))
    else:
        render_details_html = "<div class='err'>No renderer detail fields available.</div>"

    capture_source = display_capture.get("source", "app_state") if isinstance(display_capture, dict) else "app_state"

    if weather.get("valid"):
        weather_html = (
            '<div class="big">%.1fC</div>'
            '<div>%s | Feels %.1fC | Hum %s%%</div>'
            '<div>Wind %.1f m/s</div>'
        ) % (
            float(weather.get("temp_c", 0.0)),
            html.escape(str(weather.get("condition", "---"))),
            float(weather.get("feels_like_c", 0.0)),
            html.escape(str(weather.get("humidity", 0))),
            float(weather.get("wind_ms", 0.0)),
        )
    else:
        err = weather_error or "(no data)"
        weather_html = '<div class="err">OWM error: %s</div>' % html.escape(str(err))

    forecast = data.get("forecast", [])
    fc_rows = ""
    for day in forecast[:5]:
        fc_rows += (
            "<tr><td>%s</td><td>%s</td><td>%s/%s</td><td>%s%%</td></tr>"
            % (
                html.escape(str(day.get("day", "?"))),
                html.escape(str(day.get("condition", "---"))),
                html.escape(str(day.get("temp_min", 0))),
                html.escape(str(day.get("temp_max", 0))),
                html.escape(str(day.get("humidity", 0))),
            )
        )
    forecast_html = (
        "<table><tr><th>Day</th><th>Cond.</th><th>Min/Max</th><th>Hum</th></tr>" + fc_rows + "</table>"
        if fc_rows
        else "<div class='err'>No forecast data</div>"
    )

    if metrics.get("valid"):
        pc_html = (
            _pct_bar(metrics.get("cpu_pct"), "CPU", "#4af")
            + _pct_bar(metrics.get("ram_pct"), "RAM", "#fa4")
            + _pct_bar(metrics.get("disk_pct"), "Disk", "#a4f")
            + _pct_bar(metrics.get("gpu_pct"), "GPU", "#4fa")
        )
        if metrics.get("temp_c") is not None:
            pc_html += '<div class="metric"><span>CPU temp</span><span class="val">%.1fC</span></div>' % float(
                metrics.get("temp_c", 0.0)
            )
    else:
        pc_html = '<div class="err">PC metrics not available</div>'

    if esp:
        esp_html = (
            "<div>RAM free: %s kB | CPU: %s MHz | RSSI: %s dBm</div>"
            "<div>IP: %s | Uptime: %s s</div>"
            % (
                html.escape(str(esp.get("ram_free_kb", 0))),
                html.escape(str(esp.get("cpu_mhz", 0))),
                html.escape(str(esp.get("rssi", "?"))),
                html.escape(str(esp.get("ip", "?"))),
                html.escape(str(esp.get("uptime_s", 0))),
            )
        )
    else:
        esp_html = '<div class="err">ESP status not available</div>'

    solar_html = (
        "<div>Gen %.0fW | Grid %.0fW | Bat %.0f%%</div>"
        % (
            float(solar.get("generation_w", 0.0)),
            float(solar.get("grid_w", 0.0)),
            float(solar.get("battery_soc", 0.0)),
        )
        if solar.get("valid")
        else '<div class="err">Solar disabled or no data</div>'
    )

    html_out = """<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>ESP32 Display Snapshot</title>
<style>
  body { font-family: monospace; background: #111; color: #eee; margin: 20px; }
  h1 { color: #4af; font-size: 1.1em; }
  .display { border: 2px solid #4af; border-radius: 6px; padding: 12px; width: 520px; background: #000; }
  .status { font-size: 0.85em; color: %s; margin-bottom: 8px; }
  .page-tab { background: #222; padding: 4px 10px; border-radius: 4px; display: inline-block; margin-bottom: 8px; color: #4af; }
  .big { font-size: 2.2em; font-weight: bold; color: #4af; }
  .err { color: #f66; }
  .section { margin-top: 12px; border-top: 1px solid #333; padding-top: 8px; }
  .section h2 { font-size: 0.9em; color: #aaa; margin: 0 0 6px 0; }
  table { border-collapse: collapse; font-size: 0.85em; }
  td, th { padding: 3px 8px; border: 1px solid #333; }
  th { color: #4af; }
  .metric { display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: 0.85em; }
  .metric span:first-child { width: 60px; }
  .bar { flex: 1; height: 10px; background: #333; border-radius: 3px; overflow: hidden; }
  .fill { height: 100%%; border-radius: 3px; }
  .val { width: 48px; text-align: right; }
  .render-lines { margin: 0; padding-left: 20px; font-size: 0.9em; }
  .render-lines li { margin: 3px 0; }
  pre { margin: 0; white-space: pre-wrap; word-break: break-word; color: #d6deea; font-size: 0.85em; }
  .meta { font-size: 0.8em; color: #666; margin-top: 16px; }
</style>
</head>
<body>
<h1>ESP32 Display Snapshot</h1>
<div class=\"display\">
  <div class=\"status\">WiFi: %s | Net errors: %s | Time synced: %s | Source: %s</div>
  <div class=\"page-tab\">Active page: %s - %s</div>

  <div class=\"section\">
    <h2>Rendered Display Data (actual on-screen fields)</h2>
    %s
  </div>

  <div class=\"section\">
    <h2>Render Details</h2>
    %s
  </div>

  <div class=\"section\">
    <h2>Weather</h2>
    %s
  </div>

  <div class=\"section\">
    <h2>Forecast</h2>
    %s
  </div>

  <div class=\"section\">
    <h2>PC Metrics</h2>
    %s
  </div>

  <div class=\"section\">
    <h2>Solar</h2>
    %s
  </div>

  <div class=\"section\">
    <h2>ESP Status</h2>
    %s
  </div>
</div>
<div class=\"meta\">Snapshot taken: %s | Fields: %d</div>
</body>
</html>
""" % (
        wifi_col,
        wifi,
        html.escape(str(net_streak)),
        "Yes" if time_synced else "No",
        html.escape(str(capture_source)),
        html.escape(str(page)),
        html.escape(PAGE_NAMES.get(page, "?")),
        render_lines_html,
        render_details_html,
        weather_html,
        forecast_html,
        pc_html,
        solar_html,
        esp_html,
        ts,
        len(data),
    )

    output_path.write_text(html_out, encoding="utf-8")
    print("[capture] Snapshot saved -> %s" % output_path)


def render_html_sweep(result, output_path):
    """Render a multi-page sweep report."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    blocks = []

    captures = result.get("captures", [])
    pass_count = sum(1 for item in captures if item.get("ok"))

    for item in captures:
        requested = item.get("requested_page")
        observed = item.get("observed_page")
        observed_source = item.get("observed_source", "unknown")
        ok = bool(item.get("ok"))
        status = "PASS" if ok else "FAIL"
        colour = "#4f4" if ok else "#f66"
        err = item.get("error") or ""

        rows.append(
            "<tr>"
            "<td>%s</td><td>%s</td><td>%s</td><td style='color:%s'>%s</td><td>%s</td>"
            "</tr>"
            % (
                html.escape(str(requested)),
                html.escape(str(observed)),
                html.escape(str(observed_source)),
                colour,
                status,
                html.escape(err),
            )
        )

        snap = item.get("snapshot")
        if snap is None:
            blocks.append(
                "<div class='card'><h3>Page %s (%s)</h3><div class='err'>%s</div></div>"
                % (
                    html.escape(str(requested)),
                    html.escape(PAGE_NAMES.get(requested, "?")),
                    html.escape(err or "No snapshot"),
                )
            )
            continue

        render_payload = _extract_display_capture(snap)
        pretty_payload = render_payload if render_payload else snap
        if render_payload:
            source_label = render_payload.get("source", "display_capture")
        else:
            source_label = "app_state"
        pretty = html.escape(json.dumps(pretty_payload, indent=2, sort_keys=True))
        blocks.append(
            "<div class='card'>"
            "<h3>Page %s (%s) -> observed %s (source: %s)</h3>"
            "<pre>%s</pre>"
            "</div>"
            % (
                html.escape(str(requested)),
                html.escape(PAGE_NAMES.get(requested, "?")),
                html.escape(str(observed)),
                html.escape(source_label),
                pretty,
            )
        )

    error_banner = ""
    if result.get("error"):
        error_banner = "<div class='err'>Global error: %s</div>" % html.escape(result["error"])

    html_out = """<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>ESP32 Menu Sweep Report</title>
<style>
  body { font-family: monospace; background: #101216; color: #e8ecf1; margin: 20px; }
  h1 { color: #4af; font-size: 1.2em; }
  h2 { color: #9cc5ff; font-size: 1em; }
  table { border-collapse: collapse; width: 100%%; max-width: 840px; margin-bottom: 16px; }
  th, td { border: 1px solid #2a3342; padding: 6px 8px; text-align: left; }
  th { background: #1b2330; color: #9cc5ff; }
  .err { color: #ff8a8a; margin: 10px 0; }
  .meta { color: #8796ab; font-size: 0.9em; margin-bottom: 10px; }
  .grid { display: grid; gap: 12px; }
  .card { border: 1px solid #2a3342; border-radius: 8px; background: #0c1016; padding: 10px; }
  .card h3 { margin: 0 0 8px 0; color: #9cc5ff; font-size: 0.95em; }
  pre { margin: 0; white-space: pre-wrap; word-break: break-word; color: #d6deea; font-size: 0.85em; }
</style>
</head>
<body>
<h1>ESP32 Menu Sweep Report</h1>
<div class=\"meta\">Generated: %s | Pass: %d/%d | Enabled pages: %s</div>
%s
<h2>Summary</h2>
<table>
    <tr><th>Requested</th><th>Observed</th><th>Source</th><th>Status</th><th>Details</th></tr>
  %s
</table>
<h2>Snapshots</h2>
<div class=\"grid\">%s</div>
</body>
</html>
""" % (
        ts,
        pass_count,
        len(captures),
        html.escape(str(result.get("enabled_pages"))),
        error_banner,
        "".join(rows),
        "".join(blocks),
    )

    output_path.write_text(html_out, encoding="utf-8")
    print("[capture] Sweep report saved -> %s" % output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_pages_arg(raw):
    if not raw:
        return None

    pages = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            page = int(token)
        except ValueError as exc:
            raise ValueError("invalid page token: %s" % token) from exc
        if page < 0 or page > 5:
            raise ValueError("page out of range (0-5): %d" % page)
        if page not in pages:
            pages.append(page)

    if not pages:
        raise ValueError("pages list is empty")
    return pages


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="Serial port (e.g. COM13 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--output", default="snapshot.html", help="Output HTML file (default: snapshot.html)")
    parser.add_argument("--timeout", type=int, default=15, help="Seconds to wait for each snapshot (default: 15)")
    parser.add_argument("--all-pages", action="store_true", help="Capture and validate all enabled pages")
    parser.add_argument(
        "--pages",
        default="",
        help="Optional comma-separated page ids to capture in --all-pages mode (example: 0,1,2,3,4,5)",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=300,
        help="Delay after !PAGE before !SNAP in all-pages mode (default: 300)",
    )
    args = parser.parse_args()

    output_path = Path(args.output)

    if not args.all_pages:
        data = trigger_snapshot(args.port, baud=args.baud, timeout_s=args.timeout)
        if data is None:
            sys.exit(1)

        print(
            "[capture] Page: %s  WiFi: %s  Weather valid: %s"
            % (
                data.get("page"),
                data.get("wifi_online"),
                data.get("weather", {}).get("valid"),
            )
        )
        render_html(data, output_path)
        return

    try:
        page_override = _parse_pages_arg(args.pages)
    except ValueError as exc:
        print("[capture] ERROR: %s" % exc)
        sys.exit(2)

    result = capture_page_sweep(
        args.port,
        baud=args.baud,
        timeout_s=args.timeout,
        settle_ms=args.settle_ms,
        page_override=page_override,
    )
    render_html_sweep(result, output_path)

    if result.get("error"):
        print("[capture] FAIL: %s" % result["error"])
        sys.exit(1)

    failures = [item for item in result.get("captures", []) if not item.get("ok")]
    if failures:
        print("[capture] FAIL: %d page checks failed" % len(failures))
        for item in failures:
            print(
                "  page %s -> observed %s (%s)"
                % (
                    item.get("requested_page"),
                    item.get("observed_page"),
                    item.get("error"),
                )
            )
        sys.exit(1)

    print("[capture] PASS: all %d requested pages captured" % len(result.get("captures", [])))


if __name__ == "__main__":
    main()
