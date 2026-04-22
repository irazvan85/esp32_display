"""SolarMan Cloud API client with token refresh on HTTP 401."""

try:
    import ujson as json
except ImportError:
    import json

try:
    requests = __import__("urequests")
except ImportError:
    requests = None

from compat import ticks_ms


class SolarService:
    def __init__(self, cfg):
        self._cfg = cfg
        self._token = ""
        self._token_ok = False

    def enabled(self):
        return bool(self._cfg.get("solar", {}).get("enabled", False))

    def fetch_realtime(self):
        if not self.enabled():
            return None
        if requests is None:
            raise RuntimeError("urequests is not installed")

        if not self._token_ok:
            if not self._fetch_token():
                return None

        solar_cfg = self._cfg["solar"]
        body = json.dumps({"stationId": int(solar_cfg["station_id"])})
        url = "https://globalapi.solarmanpv.com/station/v1.0/realTime"

        for attempt in range(2):
            response = None
            try:
                response = requests.post(
                    url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "bearer %s" % self._token,
                    },
                )
                if response.status_code == 401 and attempt == 0:
                    self._token_ok = False
                    if not self._fetch_token():
                        return None
                    continue

                if response.status_code != 200:
                    raise RuntimeError("Solar HTTP %s" % response.status_code)

                payload = response.json()
                return {
                    "valid": True,
                    "generation_w": float(payload.get("generationPower", 0.0)),
                    "grid_w": float(payload.get("gridPower", 0.0)),
                    "battery_soc": float(payload.get("batterySoc", 0.0)),
                    "last_update_ts": int(payload.get("lastUpdateTime", 0)),
                    "fetched_ms": ticks_ms(),
                }
            finally:
                if response is not None:
                    response.close()

        return None

    def _fetch_token(self):
        solar_cfg = self._cfg["solar"]
        url = (
            "https://globalapi.solarmanpv.com/account/v1.0/token"
            "?appId=%s&language=en" % solar_cfg["app_id"]
        )
        body = json.dumps(
            {
                "appSecret": solar_cfg["app_secret"],
                "email": solar_cfg["email"],
                "password": solar_cfg["pass_sha256"],
            }
        )

        response = None
        try:
            response = requests.post(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code != 200:
                print("[Solar] Token HTTP %s" % response.status_code)
                return False

            payload = response.json()
            token = payload.get("access_token", "")
            if not token:
                print("[Solar] Token missing")
                return False

            self._token = token
            self._token_ok = True
            print("[Solar] Token OK")
            return True
        except OSError as exc:
            print("[Solar] Token error: %s" % exc)
            return False
        finally:
            if response is not None:
                response.close()
