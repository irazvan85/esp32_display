"""PC system metrics client over local HTTP."""

try:
    requests = __import__("urequests")
except ImportError:
    requests = None

from compat import ticks_ms


class MetricsService:
    def __init__(self, cfg):
        self._cfg = cfg

    def enabled(self):
        return bool(self._cfg.get("metrics", {}).get("enabled", False))

    def fetch(self):
        if requests is None:
            raise RuntimeError("urequests is not installed")

        metrics_cfg = self._cfg["metrics"]
        url = metrics_cfg["pc_url"]
        timeout_ms = int(metrics_cfg.get("timeout_ms", 3_000))
        timeout_s = max(1, timeout_ms // 1000)

        response = None
        try:
            response = self._get(url, timeout_s)
            if response.status_code != 200:
                raise RuntimeError("PC metrics HTTP %s" % response.status_code)

            payload = response.json()
            return {
                "valid": bool(payload.get("valid", True)),
                "cpu_pct": self._pct(payload.get("cpu_pct", 0.0)),
                "ram_pct": self._pct(payload.get("ram_pct", 0.0)),
                "disk_pct": self._pct(payload.get("disk_pct", 0.0)),
                "temp_c": self._opt_float(payload.get("temp_c", None)),
                "uptime_s": int(payload.get("uptime_s", 0)),
                "ts": int(payload.get("ts", 0)),
                "fetched_ms": ticks_ms(),
            }
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _pct(value):
        pct = float(value)
        if pct < 0.0:
            return 0.0
        if pct > 100.0:
            return 100.0
        return pct

    @staticmethod
    def _opt_float(value):
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _get(url, timeout_s):
        try:
            return requests.get(url, timeout=timeout_s)
        except TypeError:
            return requests.get(url)
