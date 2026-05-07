"""OpenWeatherMap API client.

This implementation is intentionally compact for MicroPython RAM limits.
"""

try:
    requests = __import__("urequests")
except ImportError:
    requests = None

try:
    import ujson as _json
except ImportError:
    import json as _json

from compat import ticks_ms
import gc


_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

OWM_HOST = "api.openweathermap.org"

# Pre-resolved socket address for OWM.  Set by pre_resolve_owm() before
# display init while DMA-capable RAM is still plentiful.
#
# After DisplayManager.init() the SPI DMA buffers consume all contiguous
# DMA-capable RAM so lwIP's getaddrinfo() fails with EAI_MEMORY (-203) for
# ANY host — even IP literals — because the DNS infrastructure allocates
# from the same pool.  Storing the raw addrinfo tuple here lets _get() use
# raw sockets with sock.connect(_OWM_CACHED_ADDR) which bypasses
# getaddrinfo entirely (MicroPython's socket.connect resolves a string-tuple
# via netutils_parse_ipv4_addr, not getaddrinfo).
_OWM_CACHED_ADDR = None  # (bytes4, port) or ('ip_str', port) — set by pre_resolve_owm()
_OWM_CACHED_IP = None    # human-readable IP string for logging


def pre_resolve_owm():
    """Resolve OWM hostname while DMA RAM is still available (before display init).

    Stores the full addrinfo tuple in _OWM_CACHED_ADDR.  WeatherService._get()
    uses this tuple to connect via raw socket, completely bypassing getaddrinfo.
    """
    global _OWM_CACHED_ADDR, _OWM_CACHED_IP
    try:
        try:
            import usocket as _sock
        except ImportError:
            import socket as _sock
        info = _sock.getaddrinfo(OWM_HOST, 80, 0, _sock.SOCK_STREAM)
        raw_addr = info[0][-1]  # ('5.9.82.93', 80)
        ip_str = raw_addr[0] if isinstance(raw_addr, tuple) else str(raw_addr)
        _OWM_CACHED_IP = ip_str
        # Store IP as 4-byte bytes (network/big-endian order).
        # MicroPython modlwip.c netutils_parse_ipv4_addr() has a len==4 fast
        # path that copies bytes directly without calling lwip_getaddrinfo.
        # This fully bypasses the MEMP_NETDB allocation that fails with
        # EAI_MEMORY (-203) after the display SPI DMA buffers are allocated.
        try:
            ip_bytes = bytes(int(x) for x in ip_str.split("."))
            if len(ip_bytes) == 4:
                _OWM_CACHED_ADDR = (ip_bytes, 80)
            else:
                _OWM_CACHED_ADDR = raw_addr  # fallback: string tuple
        except Exception:
            _OWM_CACHED_ADDR = raw_addr  # fallback: string tuple
        print("[OWM] pre-resolved %s -> %s" % (OWM_HOST, _OWM_CACHED_IP))
    except Exception as exc:
        print("[OWM] pre-resolve failed: %s" % exc)
    finally:
        gc.collect()


class _OWMResponse:
    """Minimal urequests-compatible response backed by raw bytes."""
    __slots__ = ("status_code", "_body")

    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return _json.loads(self._body)

    def close(self):
        pass


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
        """GET with raw-socket fast-path and urequests fallback.

        When _OWM_CACHED_ADDR is set (pre-resolved before display init),
        uses a raw socket and calls sock.connect(_OWM_CACHED_ADDR) directly.
        pre_resolve_owm() stores the IP as 4-byte bytes so that modlwip.c's
        netutils_parse_ipv4_addr() uses its len==4 fast path, bypassing
        lwip_getaddrinfo() entirely.  getaddrinfo() fails with EAI_MEMORY
        (-203) after display SPI DMA buffers exhaust the MEMP_NETDB pool.
        When _OWM_CACHED_ADDR retries are exhausted the last exception is
        raised immediately; requests.get() fallback is NOT used (it would also
        call getaddrinfo and fail identically).

        Falls back to urequests when the cached address is absent (first boot,
        bootstrap phase, or host-side tests where the fast-path isn't needed).

        Retries up to 3 times on transient DNS errors:
          OSError(-202) = EAI_FAIL  — DNS server returned failure
          OSError(-203) = EAI_MEMORY — DNS resolver out of heap memory
        """
        if _OWM_CACHED_ADDR is not None:
            _last_socket_exc = None
            for attempt in range(3):
                if attempt > 0:
                    gc.collect()
                try:
                    return WeatherService._get_socket(url, _OWM_CACHED_ADDR, OWM_HOST, timeout_s)
                except OSError as exc:
                    code = exc.args[0] if exc.args else None
                    if code in (-202, -203):
                        _last_socket_exc = exc
                        continue  # retry on transient DNS-style errors
                    raise
            # All retries exhausted — raise instead of falling through to
            # requests.get() which also calls getaddrinfo and will also fail.
            if _last_socket_exc is not None:
                raise _last_socket_exc

        if requests is None:
            raise RuntimeError("urequests unavailable and no cached OWM address")

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

    @staticmethod
    def _get_socket(url, addr, host, timeout_s):
        """Raw-socket HTTP GET that connects via a pre-resolved address tuple.

        sock.connect(addr) where addr = ('ip', port) uses MicroPython's
        netutils_parse_ipv4_addr internally — no getaddrinfo call.
        """
        try:
            import usocket as _socket
        except ImportError:
            import socket as _socket

        # Extract path+query from URL.  URL is always http://<host>/<path>.
        after_scheme = url[7:]  # strip "http://"
        slash = after_scheme.find("/")
        path = after_scheme[slash:] if slash >= 0 else "/"

        sock = None
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            try:
                sock.settimeout(timeout_s)
            except Exception:
                pass
            sock.connect(addr)

            req = (
                "GET %s HTTP/1.0\r\n"
                "Host: %s\r\n"
                "Connection: close\r\n"
                "Accept: application/json\r\n"
                "\r\n"
            ) % (path, host)
            sock.send(req.encode("utf-8"))

            # Read up to 28 KB — enough for current (~1.5 KB) and forecast
            # (~15-20 KB).  MicroPython gc.collect() is called by callers.
            MAX_BYTES = 28672
            chunks = []
            total = 0
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise RuntimeError("OWM response too large (>%d)" % MAX_BYTES)
                chunks.append(chunk)

            if not chunks:
                raise RuntimeError("OWM empty response")

            raw = b"".join(chunks)
            sep = raw.find(b"\r\n\r\n")
            if sep < 0:
                raise RuntimeError("OWM missing header separator")

            status_line = raw[: raw.find(b"\r\n")].decode("utf-8")
            parts = status_line.split(" ", 2)
            status_code = int(parts[1]) if len(parts) >= 2 else 0

            body = raw[sep + 4 :]
            return _OWMResponse(status_code, body)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
