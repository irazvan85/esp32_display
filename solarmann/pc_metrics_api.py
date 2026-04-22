"""Lightweight local HTTP API for PC system metrics.

Run on the same LAN as the ESP32 board. Endpoint:
  GET /api/system/metrics
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import psutil

try:
    import wmi as _wmi  # Windows only — installed by start_pc_monitor.ps1
    _WMI_AVAILABLE = True
except Exception:
    _WMI_AVAILABLE = False


def _pick_temperature_c() -> Optional[float]:
    """Best-effort temperature extraction across platforms/sensors.

    Strategy (in order):
    1. psutil.sensors_temperatures()         — Linux / macOS
    2. WMI MSAcpi_ThermalZoneTemperature     — Windows (ACPI zones, tenths-K)
    3. WMI Win32_TemperatureProbe            — Windows (CIM probe, tenths-K)

    Returns None when no source is available; the display shows "N/A" in that
    case.  On many gaming/Lenovo laptops all WMI paths return None — use
    LibreHardwareMonitor (lhm_bridge option planned) for full coverage.
    """
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


def _collect_metrics(path: str) -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(path)
    cpu = psutil.cpu_percent(interval=None)
    uptime_s = max(0, int(time.time() - psutil.boot_time()))

    return {
        "valid": True,
        "cpu_pct": round(float(cpu), 1),
        "ram_pct": round(float(vm.percent), 1),
        "disk_pct": round(float(disk.percent), 1),
        "temp_c": _pick_temperature_c(),
        "uptime_s": uptime_s,
        "ts": int(time.time()),
    }


class _Handler(BaseHTTPRequestHandler):
    metrics_path = "/"

    def do_GET(self):
        if self.path != "/api/system/metrics":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not_found"}')
            return

        payload = _collect_metrics(self.metrics_path)
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
    args = parser.parse_args()

    _Handler.metrics_path = args.disk_path
    server = ThreadingHTTPServer((args.host, args.port), _Handler)

    print("[pc-api] Serving on http://%s:%d/api/system/metrics" % (args.host, args.port))
    print("[pc-api] Monitoring disk path: %s" % args.disk_path)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[pc-api] Stopped")


if __name__ == "__main__":
    main()
