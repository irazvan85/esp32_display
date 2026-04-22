"""Lightweight local HTTP API for PC system metrics.

Run on the same LAN as the ESP32 board. Endpoint:
  GET /api/system/metrics
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import psutil

try:
    import wmi as _wmi  # Windows only — installed by start_pc_monitor.ps1
    _WMI_AVAILABLE = True
except Exception:
    _WMI_AVAILABLE = False


# ---------------------------------------------------------------------------
# LibreHardwareMonitor REST API helpers
# ---------------------------------------------------------------------------

_lhm_cache: dict = {"sensors": [], "ts": 0.0}
_LHM_CACHE_TTL = 5.0  # seconds — avoid hammering LHM on every metrics call


def _extract_sensors_from_tree(node: dict, result: list | None = None) -> list:
    """Recursively flatten a LibreHardwareMonitor JSON sensor tree."""
    if result is None:
        result = []
    if "SensorId" in node:
        result.append(node)
    for child in node.get("Children", []):
        _extract_sensors_from_tree(child, result)
    return result


def _lhm_parse_value(value_str: str) -> Optional[float]:
    """Parse a float from an LHM value string like '45.0 °C' or '85 %'."""
    m = re.search(r"[-+]?\d*\.?\d+", str(value_str))
    return float(m.group()) if m else None


def _lhm_get_sensors(lhm_url: str) -> list:
    """Fetch and cache sensors from the LHM REST API data.json endpoint.

    Returns the cached list (possibly empty) on error so callers always
    receive a plain list. Cache TTL is 5 s — one fetch covers all
    per-metric reads within a single HTTP request cycle.
    """
    now = time.monotonic()
    if now - _lhm_cache["ts"] < _LHM_CACHE_TTL and _lhm_cache["sensors"]:
        return _lhm_cache["sensors"]
    try:
        req = urllib.request.Request(lhm_url, method="GET")
        req.add_header("User-Agent", "pc-metrics-api/1.0")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                root = json.loads(resp.read().decode("utf-8"))
                sensors = _extract_sensors_from_tree(root)
                _lhm_cache["sensors"] = sensors
                _lhm_cache["ts"] = now
                return sensors
    except Exception:
        pass
    return _lhm_cache["sensors"]  # return stale on error


def _lhm_is_available() -> bool:
    """True if the LHM cache has been populated at least once."""
    return bool(_lhm_cache["sensors"])


# ---------------------------------------------------------------------------


def _pick_temperature_c(lhm_url: Optional[str] = None) -> Optional[float]:
    """Best-effort CPU temperature.

    Strategy (in order):
    1. LibreHardwareMonitor REST API     — if --lhm-url configured (best for gaming laptops)
    2. psutil.sensors_temperatures()    — Linux / macOS
    3. WMI MSAcpi_ThermalZoneTemperature — Windows ACPI zones (tenths-K)
    4. WMI Win32_TemperatureProbe        — Windows CIM probe (tenths-K)

    Returns None when no source is available; the display shows "N/A".
    """
    # --- LHM path (fastest and most complete — try first) ------------------
    if lhm_url:
        sensors = _lhm_get_sensors(lhm_url)
        cpu_temps = [
            s for s in sensors
            if s.get("Type") == "Temperature"
            and "cpu" in s.get("SensorId", "").lower()
        ]
        # Prefer the most representative reading
        for kw in ("package", "core max", "core avg"):
            for s in cpu_temps:
                if kw in s.get("Text", "").lower():
                    val = _lhm_parse_value(s.get("Value", ""))
                    if val is not None:
                        return round(val, 1)
        # Fallback: first available CPU temp from LHM
        for s in cpu_temps:
            val = _lhm_parse_value(s.get("Value", ""))
            if val is not None:
                return round(val, 1)

    # --- psutil path (Linux / macOS) ----------------------------------------
    try:
        sensors = psutil.sensors_temperatures()
        if sensors:
            for readings in sensors.values():
                for reading in readings:
                    current = getattr(reading, "current", None)
                    if current is not None:
                        return float(current)
    except (AttributeError, NotImplementedError, OSError, RuntimeError):
        pass

    if not _WMI_AVAILABLE:
        return None

    # --- WMI path 1: MSAcpi_ThermalZoneTemperature (tenths of Kelvin) -------
    try:
        zones = _wmi.WMI(namespace=r"root\wmi").MSAcpi_ThermalZoneTemperature()
        if zones:
            temps = [(z.CurrentTemperature / 10.0) - 273.15 for z in zones]
            return round(sum(temps) / len(temps), 1)
    except Exception:
        pass

    # --- WMI path 2: Win32_TemperatureProbe (tenths of Kelvin, if reported) -
    try:
        probes = _wmi.WMI().Win32_TemperatureProbe()
        readings = [
            (p.CurrentReading / 10.0) - 273.15
            for p in probes
            if p.CurrentReading is not None and p.CurrentReading not in (32768, 0)
        ]
        if readings:
            return round(sum(readings) / len(readings), 1)
    except Exception:
        pass

    return None


def _pick_gpu_temp_c(lhm_url: Optional[str] = None) -> Optional[float]:
    """GPU temperature via LHM REST API. Returns None when unavailable."""
    if not lhm_url:
        return None
    sensors = _lhm_get_sensors(lhm_url)
    for s in sensors:
        if (
            s.get("Type") == "Temperature"
            and "gpu" in s.get("SensorId", "").lower()
        ):
            val = _lhm_parse_value(s.get("Value", ""))
            if val is not None:
                return round(val, 1)
    return None


def _pick_gpu_pct(lhm_url: Optional[str] = None) -> Optional[float]:
    """GPU core load % via LHM REST API. Returns None when unavailable."""
    if not lhm_url:
        return None
    sensors = _lhm_get_sensors(lhm_url)
    # Prefer GPU Core load (not memory load)
    for s in sensors:
        sid = s.get("SensorId", "").lower()
        sname = s.get("Text", "").lower()
        if (
            s.get("Type") == "Load"
            and "gpu" in sid
            and "core" in sname
            and "memory" not in sname
        ):
            val = _lhm_parse_value(s.get("Value", ""))
            if val is not None:
                return round(val, 1)
    # Fallback: first GPU Load sensor
    for s in sensors:
        if s.get("Type") == "Load" and "gpu" in s.get("SensorId", "").lower():
            val = _lhm_parse_value(s.get("Value", ""))
            if val is not None:
                return round(val, 1)
    return None


def _collect_metrics(path: str, lhm_url: Optional[str] = None) -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(path)
    cpu = psutil.cpu_percent(interval=None)
    uptime_s = max(0, int(time.time() - psutil.boot_time()))

    return {
        "valid": True,
        "cpu_pct": round(float(cpu), 1),
        "ram_pct": round(float(vm.percent), 1),
        "disk_pct": round(float(disk.percent), 1),
        "temp_c": _pick_temperature_c(lhm_url),
        "gpu_temp_c": _pick_gpu_temp_c(lhm_url),
        "gpu_pct": _pick_gpu_pct(lhm_url),
        "lhm_available": _lhm_is_available(),
        "uptime_s": uptime_s,
        "ts": int(time.time()),
    }


class _Handler(BaseHTTPRequestHandler):
    metrics_path = "/"
    lhm_url: Optional[str] = None

    def do_GET(self):
        if self.path != "/api/system/metrics":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not_found"}')
            return

        payload = _collect_metrics(self.metrics_path, self.__class__.lhm_url)
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

def main():
    parser = argparse.ArgumentParser(description="Local PC metrics HTTP API")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8765, help="Bind port")
    parser.add_argument(
        "--disk-path",
        default="C:\\",
        help="Disk path to monitor for disk usage percentage",
    )
    parser.add_argument(
        "--lhm-url",
        default="http://localhost:8085/data.json",
        help=(
            "LibreHardwareMonitor REST API URL (data.json endpoint). "
            "Enables accurate CPU/GPU temperatures on systems where WMI is unavailable. "
            "Requires LHM running as Administrator with Options → Remote Web Server → Run. "
            "Pass an empty string to disable. Default: http://localhost:8085/data.json"
        ),
    )
    args = parser.parse_args()

    _Handler.metrics_path = args.disk_path
    _Handler.lhm_url = args.lhm_url if args.lhm_url else None
    server = ThreadingHTTPServer((args.host, args.port), _Handler)

    print("[pc-api] Serving on http://%s:%d/api/system/metrics" % (args.host, args.port))
    print("[pc-api] Monitoring disk path: %s" % args.disk_path)
    if _Handler.lhm_url:
        print("[pc-api] LHM REST API: %s" % _Handler.lhm_url)
        print("[pc-api]   → LibreHardwareMonitor must be running as Administrator")
        print("[pc-api]   → Enable: Options → Remote Web Server → Run (default port 8085)")
    else:
        print("[pc-api] LHM disabled — CPU temp via WMI/psutil fallback only")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[pc-api] Stopped")


if __name__ == "__main__":
    main()
