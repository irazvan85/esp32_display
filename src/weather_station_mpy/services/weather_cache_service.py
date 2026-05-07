"""Small persisted weather cache for startup fallback."""

try:
    json = __import__("ujson")
except ImportError:
    import json


class WeatherCacheService:
    def __init__(self, path="weather_cache.json"):
        self._path = path

    def load(self):
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError:
            return None
        except Exception as exc:
            print("[OWM] cache load error: %s" % exc)
            return None

        if not isinstance(payload, dict):
            return None

        weather = self._sanitize_weather(payload.get("weather"))
        forecast = self._sanitize_forecast(payload.get("forecast"))
        trend = self._sanitize_trend(payload.get("trend"))

        # Return None only if both weather and forecast are absent.
        # Forecast can be restored independently even if weather is null/invalid.
        if weather is None and not forecast:
            return None

        return {
            "weather": weather,
            "forecast": forecast,
            "trend": trend,
        }

    def save(self, weather=None, forecast=None, trend=None):
        weather_out = self._sanitize_weather(weather)
        if weather_out is None or not bool(weather_out.get("valid", False)):
            return False

        payload = {
            "v": 1,
            "weather": weather_out,
            "forecast": self._sanitize_forecast(forecast),
            "trend": self._sanitize_trend(trend),
        }

        try:
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            return True
        except Exception as exc:
            print("[OWM] cache write error: %s" % exc)
            return False

    @staticmethod
    def _as_float(value, default=0.0):
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _as_int(value, default=0):
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def _sanitize_weather(weather):
        if not isinstance(weather, dict):
            return None

        condition = str(weather.get("condition", "---"))
        if len(condition) > 24:
            condition = condition[:24]

        return {
            "valid": bool(weather.get("valid", False)),
            "temp_c": WeatherCacheService._as_float(weather.get("temp_c", 0.0)),
            "feels_like_c": WeatherCacheService._as_float(weather.get("feels_like_c", 0.0)),
            "humidity": WeatherCacheService._as_int(weather.get("humidity", 0)),
            "condition": condition,
            "condition_id": WeatherCacheService._as_int(weather.get("condition_id", 800)),
            "wind_ms": WeatherCacheService._as_float(weather.get("wind_ms", 0.0)),
            "fetched_ms": 0,
        }

    @staticmethod
    def _sanitize_forecast(forecast):
        if not isinstance(forecast, list):
            return []

        out = []
        for item in forecast:
            if not isinstance(item, dict):
                continue

            date_txt = str(item.get("date", ""))
            if len(date_txt) > 10:
                date_txt = date_txt[:10]

            day_txt = str(item.get("day", "---"))
            if len(day_txt) > 8:
                day_txt = day_txt[:8]

            cond_txt = str(item.get("condition", "---"))
            if len(cond_txt) > 16:
                cond_txt = cond_txt[:16]

            out.append(
                {
                    "date": date_txt,
                    "day": day_txt,
                    "temp_min": WeatherCacheService._as_float(item.get("temp_min", 0.0)),
                    "temp_max": WeatherCacheService._as_float(item.get("temp_max", 0.0)),
                    "humidity": WeatherCacheService._as_int(item.get("humidity", 0)),
                    "condition": cond_txt,
                    "condition_id": WeatherCacheService._as_int(item.get("condition_id", 800)),
                }
            )
            if len(out) >= 5:
                break

        return out

    @staticmethod
    def _sanitize_trend(trend):
        if not isinstance(trend, list):
            return []

        out = []
        for item in trend:
            if not isinstance(item, dict):
                continue

            out.append(
                {
                    "hour": WeatherCacheService._as_int(item.get("hour", 0)),
                    "temp_c": WeatherCacheService._as_float(item.get("temp_c", 0.0)),
                    "precip_mm": WeatherCacheService._as_float(item.get("precip_mm", 0.0)),
                }
            )
            if len(out) >= 8:
                break

        return out
