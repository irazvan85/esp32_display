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


def _pick_temperature_c() -> Optional[float]:
    """Best-effort temperature extraction across platforms/sensors."""
    try:
        sensors = psutil.sensors_temperatures()
    except (AttributeError, NotImplementedError, OSError, RuntimeError):
        return None

    if not sensors:
        return None

    for readings in sensors.values():
        for reading in readings:
            current = getattr(reading, "current", None)
            if current is not None:
                return float(current)
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
