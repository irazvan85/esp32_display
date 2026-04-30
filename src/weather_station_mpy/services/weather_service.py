"""OpenWeatherMap API client.

This implementation is intentionally compact for MicroPython RAM limits.
"""

try:
    requests = __import__("urequests")
except ImportError:
    requests = None

from compat import ticks_ms
import gc


_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


class WeatherService:
    def __init__(self, cfg):
        self._cfg = cfg
        self.dns_fail_streak = 0

    def fetch_current(self):
        if requests is None:
            raise RuntimeError("urequests is not installed")

        weather_cfg = self._cfg["weather"]
        url = (
            "http://api.openweathermap.org/data/2.5/weather"
            "?q=%s,%s&appid=%s&units=metric"
            % (
                weather_cfg["city"],
                weather_cfg["country"],
                weather_cfg["api_key"],
            )
        )

        response = None
        try:
            response = self._get(url)
            if response.status_code != 200:
                raise RuntimeError("OWM current HTTP %s" % response.status_code)

            payload = response.json()
            weather0 = self._first_weather(payload)
            main = payload.get("main", {})
            wind = payload.get("wind", {})

            self.dns_fail_streak = 0
            return {
                "valid": True,
                "temp_c": float(main.get("temp", 0.0)),
                "feels_like_c": float(main.get("feels_like", 0.0)),
                "humidity": int(main.get("humidity", 0)),
                "condition": str(weather0.get("main", "---")),
                "condition_id": int(weather0.get("id", 800)),
                "wind_ms": float(wind.get("speed", 0.0)),
                "fetched_ms": ticks_ms(),
            }
        except OSError as exc:
            code = exc.args[0] if exc.args else None
            if code in (-202, -203):
                self.dns_fail_streak += 1
            raise
        finally:
            if response is not None:
                response.close()

    def fetch_forecast(self):
        daily, _trend = self.fetch_forecast_bundle()
        return daily

    def fetch_forecast_bundle(self):
        if requests is None:
            raise RuntimeError("urequests is not installed")

        weather_cfg = self._cfg["weather"]
        url = (
            "http://api.openweathermap.org/data/2.5/forecast"
            "?q=%s,%s&appid=%s&units=metric&cnt=40"
            % (
                weather_cfg["city"],
                weather_cfg["country"],
                weather_cfg["api_key"],
            )
        )

        response = None
        try:
            response = self._get(url)
            if response.status_code != 200:
                raise RuntimeError("OWM forecast HTTP %s" % response.status_code)

            payload = response.json()
            entries = payload.get("list", [])
            daily = self._aggregate(entries)
            trend = self._extract_today_trend(entries)
            self.dns_fail_streak = 0
            return daily, trend
        except OSError as exc:
            code = exc.args[0] if exc.args else None
            if code in (-202, -203):
                self.dns_fail_streak += 1
            raise
        finally:
            if response is not None:
                response.close()

    def _extract_today_trend(self, entries):
        trend = []
        first_date = None

        for entry in entries:
            dt_txt = entry.get("dt_txt", "")
            if len(dt_txt) < 13:
                continue

            date_key = dt_txt[0:10]
            if first_date is None:
                first_date = date_key
            if date_key != first_date:
                continue

            hour_txt = dt_txt[11:13]
            try:
                hour = int(hour_txt)
            except ValueError:
                continue

            main = entry.get("main", {})
            rain = entry.get("rain", {})
            snow = entry.get("snow", {})
            precip_mm = float(rain.get("3h", 0.0)) + float(snow.get("3h", 0.0))

            trend.append(
                {
                    "hour": hour,
                    "temp_c": float(main.get("temp", 0.0)),
                    "precip_mm": precip_mm,
                }
            )
            if len(trend) >= 8:
                break

        return trend

    def _aggregate(self, entries):
        out = []
        by_date = {}

        for entry in entries:
            dt_txt = entry.get("dt_txt", "")
            if len(dt_txt) < 10:
                continue

            date_key = dt_txt[0:10]
            main = entry.get("main", {})
            weather0 = self._first_weather(entry)
            temp = float(main.get("temp", 0.0))
            t_min = float(main.get("temp_min", temp))
            t_max = float(main.get("temp_max", temp))

            slot = by_date.get(date_key)
            if slot is None:
                slot = {
                    "date": date_key,
                    "day": self._day_name(date_key),
                    "temp_min": t_min,
                    "temp_max": t_max,
                    "humidity": int(main.get("humidity", 0)),
                    "condition": str(weather0.get("main", "---")),
                    "condition_id": int(weather0.get("id", 800)),
                }
                by_date[date_key] = slot
                out.append(slot)
            else:
                if t_min < slot["temp_min"]:
                    slot["temp_min"] = t_min
                if t_max > slot["temp_max"]:
                    slot["temp_max"] = t_max

                # Prefer noon sample for representative condition.
                if "12:00:00" in dt_txt:
                    slot["humidity"] = int(main.get("humidity", slot["humidity"]))
                    slot["condition"] = str(weather0.get("main", slot["condition"]))
                    slot["condition_id"] = int(weather0.get("id", slot["condition_id"]))

            if len(out) >= 5:
                break

        return out

    def reset_dns_cache(self):
        """Called by weather_task after repeated DNS failures to signal a reset cycle."""
        pass  # hook for future DNS cache management

    @staticmethod
    def _day_name(date_str):
        # Sakamoto algorithm: 0=Sunday ... 6=Saturday.
        y = int(date_str[0:4])
        m = int(date_str[5:7])
        d = int(date_str[8:10])
        t = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
        if m < 3:
            y -= 1
        idx = (y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7
        return _DAYS[idx]

    @staticmethod
    def _first_weather(item):
        weather = item.get("weather")
        if isinstance(weather, (list, tuple)) and weather:
            return weather[0]
        return {}

    @staticmethod
    def _get(url, timeout_s=3):
        """GET with timeout and DNS-failure retry.

        Retries up to 3 times on transient DNS errors:
          OSError(-202) = EAI_FAIL  — DNS server returned failure
          OSError(-203) = EAI_MEMORY — DNS resolver out of heap memory

        A gc.collect() is run before each retry to free fragmented heap.
        Other OSErrors propagate immediately.
        """
        last_exc = None
        for attempt in range(3):
            if attempt > 0:
                gc.collect()
            try:
                try:
                    return requests.get(url, timeout=timeout_s)
                except TypeError:
                    return requests.get(url)
            except OSError as exc:
                code = exc.args[0] if exc.args else None
                if code in (-202, -203):
                    last_exc = exc
                    continue
                raise
        raise last_exc
