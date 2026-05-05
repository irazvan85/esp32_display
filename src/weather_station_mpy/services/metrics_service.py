"""PC system metrics client over local HTTP."""

try:
    requests = __import__("urequests")
except ImportError:
    requests = None

try:
    socket = __import__("usocket")
except ImportError:
    try:
        socket = __import__("socket")
    except ImportError:
        socket = None

try:
    json = __import__("ujson")
except ImportError:
    json = __import__("json")

from compat import ticks_ms


class MetricsService:
    def __init__(self, cfg):
        self._cfg = cfg

    def enabled(self):
        return bool(self._cfg.get("metrics", {}).get("enabled", False))

    def fetch(self):
        metrics_cfg = self._cfg["metrics"]
        url = metrics_cfg["pc_url"]
        timeout_ms = int(metrics_cfg.get("timeout_ms", 3_000))
        timeout_s = max(1, timeout_ms // 1000)
        host, port, path = self._parse_http_url(url)

        if self._is_ipv4_literal(host):
            try:
                payload = self._fetch_json_socket(host, port, path, timeout_s)
                return self._map_payload(payload)
            except Exception as _sock_exc:
                # On MicroPython (ESP32) never fall back to urequests for IP
                # literals: urequests always calls getaddrinfo which fails with
                # EAI_MEMORY (-203) after display SPI DMA buffers are allocated.
                # On CPython (host tests) urequests isn't present so this path
                # is a no-op anyway.
                try:
                    import sys as _sys
                    if _sys.implementation.name == "micropython":
                        raise
                except ImportError:
                    pass
                if requests is None:
                    raise

        if requests is None:
            raise RuntimeError("urequests is not installed")

        response = None
        try:
            response = self._get(url, timeout_s)
            if response.status_code != 200:
                raise RuntimeError("PC metrics HTTP %s" % response.status_code)

            payload = response.json()
            return self._map_payload(payload)
        finally:
            if response is not None:
                response.close()

    def _map_payload(self, payload):
        return {
            "valid": bool(payload.get("valid", True)),
            "cpu_pct": self._pct(payload.get("cpu_pct", 0.0)),
            "ram_pct": self._pct(payload.get("ram_pct", 0.0)),
            "disk_pct": self._pct(payload.get("disk_pct", 0.0)),
            "temp_c": self._opt_float(payload.get("temp_c", None)),
            "gpu_pct": self._opt_pct(payload.get("gpu_pct", None)),
            "gpu_temp_c": self._opt_float(payload.get("gpu_temp_c", None)),
            "uptime_s": int(payload.get("uptime_s", 0)),
            "ts": int(payload.get("ts", 0)),
            "fetched_ms": ticks_ms(),
        }

    def connectivity_diag(self):
        metrics_cfg = self._cfg.get("metrics", {})
        url = metrics_cfg.get("pc_url", "")
        host, port, path = self._parse_http_url(url)

        diag = {
            "url": url,
            "host": host,
            "port": port,
            "path": path,
            "ifconfig": None,
            "resolve_ok": False,
            "connect_ok": False,
            "resolve_error": None,
            "connect_error": None,
        }

        try:
            network = __import__("network")
            wlan = network.WLAN(network.STA_IF)
            diag["ifconfig"] = wlan.ifconfig()
        except Exception:
            pass

        if socket is None or not host:
            if socket is None:
                diag["resolve_error"] = "socket unavailable"
                diag["connect_error"] = "socket unavailable"
            else:
                diag["resolve_error"] = "empty host"
                diag["connect_error"] = "empty host"
            return diag

        if self._is_ipv4_literal(host):
            addr = (host, port)
            diag["resolve_ok"] = True
            diag["resolve_addr"] = addr
        else:
            addr = None
            try:
                info = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
                addr = info[0][-1]
                diag["resolve_ok"] = True
                diag["resolve_addr"] = addr
            except Exception as exc:
                diag["resolve_error"] = str(exc)
                diag["connect_error"] = "resolve failed"
                return diag

        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(2)
            except Exception:
                pass
            sock.connect(addr)
            diag["connect_ok"] = True
        except Exception as exc:
            diag["connect_error"] = str(exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

        return diag

    @staticmethod
    def _parse_http_url(url):
        raw = "" if url is None else str(url).strip()
        if not raw:
            return "", 80, "/"

        if raw.startswith("http://"):
            rest = raw[7:]
        elif "://" in raw:
            rest = raw.split("://", 1)[1]
        else:
            rest = raw

        slash = rest.find("/")
        if slash >= 0:
            host_port = rest[:slash]
            path = rest[slash:]
        else:
            host_port = rest
            path = "/"

        if not path:
            path = "/"

        host = host_port
        port = 80
        if ":" in host_port:
            maybe_host, maybe_port = host_port.rsplit(":", 1)
            if maybe_host:
                host = maybe_host
            try:
                port = int(maybe_port)
            except Exception:
                port = 80

        if port <= 0:
            port = 80

        return host, port, path

    @staticmethod
    def _is_ipv4_literal(host: str) -> bool:
        if not host:
            return False

        parts = host.split(".")
        if len(parts) != 4:
            return False

        for part in parts:
            if not part or not part.isdigit():
                return False
            try:
                octet = int(part)
            except Exception:
                return False
            if octet < 0 or octet > 255:
                return False

        return True

    @staticmethod
    def _fetch_json_socket(host, port, path, timeout_s):
        if socket is None:
            raise RuntimeError("socket unavailable")

        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(timeout_s)
            except Exception:
                pass

            sock.connect((host, port))

            if not path:
                path = "/"

            req = (
                "GET %s HTTP/1.1\r\n"
                "Host: %s\r\n"
                "Connection: close\r\n"
                "Accept: application/json\r\n"
                "\r\n"
            ) % (path, host)
            sock.send(req.encode("utf-8"))

            max_bytes = 8192
            chunks = []
            total = 0
            while True:
                chunk = sock.recv(512)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("PC metrics response too large")
                chunks.append(chunk)

            if not chunks:
                raise RuntimeError("PC metrics empty response")

            raw = b"".join(chunks)
            sep = raw.find(b"\r\n\r\n")
            if sep < 0:
                raise RuntimeError("PC metrics malformed response")

            head = raw[:sep]
            body = raw[sep + 4 :]

            line_end = head.find(b"\r\n")
            if line_end < 0:
                status_line = head
            else:
                status_line = head[:line_end]

            try:
                parts = status_line.decode("utf-8").split(" ")
                status_code = int(parts[1])
            except Exception:
                raise RuntimeError("PC metrics malformed status line")

            if status_code != 200:
                raise RuntimeError("PC metrics HTTP %s" % status_code)

            if not body:
                raise RuntimeError("PC metrics empty body")

            try:
                return json.loads(body.decode("utf-8"))
            except Exception:
                raise RuntimeError("PC metrics malformed JSON")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    @staticmethod
    def _pct(value):
        pct = float(value)
        if pct < 0.0:
            return 0.0
        if pct > 100.0:
            return 100.0
        return pct

    @staticmethod
    def _opt_pct(value):
        if value is None:
            return None
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
