"""Retro Weather Clock for MicroPython (initial implementation).

This is phase-1/2 migration code: hardware, config persistence, async scheduler,
and service wiring are implemented. Rendering and service behavior are intentionally
kept simple while parity work continues.
"""

import gc

try:
    asyncio = __import__("uasyncio")
except ImportError:
    import asyncio

try:
    _machine = __import__("machine")
    Pin = _machine.Pin
except ImportError:
    class Pin:
        IN = 0
        PULL_UP = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def value(self):
            return 1

import board
from app_state import AppState
from compat import mem_free, mem_alloc, ticks_diff, ticks_ms
from config.store import ConfigNotReadyError, load_config
from services.time_service import TimeService
from services.wifi_service import WifiService


def _normalize_enabled_pages(value):
    if not isinstance(value, list):
        return [0, 1, 2, 3, 4, 5]

    pages = []
    for item in value:
        if not isinstance(item, int):
            continue
        if 0 <= item <= 5 and item not in pages:
            pages.append(item)

    pages.sort()
    if not pages:
        return [0, 1, 2, 3, 4, 5]
    return pages


def _next_enabled_page(current_page, enabled_pages):
    pages = _normalize_enabled_pages(enabled_pages)
    if current_page in pages:
        idx = pages.index(current_page)
        return pages[(idx + 1) % len(pages)]
    return pages[0]


def _apply_theme_safe(theme_name):
    try:
        from ui.theme import apply_theme

        return apply_theme(theme_name)
    except MemoryError:
        gc.collect()
        print("[WEB] theme apply MemoryError; keeping current palette")
        return "retro"
    except Exception as exc:
        print("[WEB] theme apply error: %s" % exc)
        return "retro"


async def button_task(state):
    # Queue press events in an IRQ-safe byte so bursts are not lost while
    # blocking network calls run in other tasks.
    _press_q = bytearray(1)
    button = Pin(board.BTN_PIN, Pin.IN, Pin.PULL_UP)

    def _irq(_pin):
        if _press_q[0] < 255:
            _press_q[0] += 1

    button.irq(trigger=Pin.IRQ_FALLING, handler=_irq)
    last_press_ms = 0

    while True:
        if _press_q[0]:
            now = ticks_ms()
            if ticks_diff(now, last_press_ms) > board.BTN_DEBOUNCE_MS:
                _press_q[0] -= 1
                last_press_ms = now
                if state.page == board.PAGE_PC_MONITOR:
                    gpu_available = state.metrics.get("gpu_pct") is not None
                    if gpu_available and state.metrics_subpage == 0:
                        state.metrics_subpage = 1
                        state.metrics_dirty = True
                        print("[BTN] PC view B (GPU)")
                    else:
                        state.metrics_subpage = 0
                        state.page = _next_enabled_page(state.page, state.enabled_pages)
                        state.page_dirty = True
                        print("[BTN] Page -> %d" % state.page)
                else:
                    state.page = _next_enabled_page(state.page, state.enabled_pages)
                    state.page_dirty = True
                    print("[BTN] Page -> %d" % state.page)
        await asyncio.sleep_ms(20)


async def wifi_task(state, wifi_svc, cfg, time_svc):
    wifi_check_ms = int(cfg["wifi"].get("check_interval_ms", 30_000))
    wifi_offline_retry_ms = int(cfg["wifi"].get("offline_retry_ms", 5_000))

    while True:
        online = await wifi_svc.ensure_connected()
        changed = online != state.wifi_online
        state.wifi_online = online

        if changed:
            state.status_dirty = True
            state.page_dirty = True
            if online:
                state.time_synced = await time_svc.sync_ntp()

        if online:
            await asyncio.sleep_ms(wifi_check_ms)
        else:
            await asyncio.sleep_ms(wifi_offline_retry_ms)


async def weather_task(state, weather_svc, cfg):
    if not bool(cfg["weather"].get("enabled", True)):
        print("[OWM] Disabled in config")
        return

    refresh_ms = int(cfg["weather"].get("refresh_ms", 600_000))
    retry_ms = int(cfg["weather"].get("retry_ms", 30_000))
    offline_retry_ms = int(cfg["weather"].get("offline_retry_ms", 5_000))

    # If startup bootstrap already seeded weather, defer the first periodic
    # fetch to avoid a back-to-back HTTP+parse that exhausts fragmented heap.
    if state.last_weather_fetch_ms and state.last_forecast_fetch_ms:
        print("[OWM] bootstrap data present, deferring first fetch by refresh_ms")
        await asyncio.sleep_ms(refresh_ms)

    while True:
        if not state.wifi_online:
            await asyncio.sleep_ms(offline_retry_ms)
            continue

        try:
            weather = weather_svc.fetch_current()
            gc.collect()  # free parsed JSON payloads

            state.weather = weather
            state.weather_dirty = True
            state.status_dirty = True
            state.weather_error = ""
            state.last_weather_fetch_ms = weather["fetched_ms"]

            print(
                "[OWM] %.1fC %s Hum:%d%%"
                % (
                    state.weather["temp_c"],
                    state.weather["condition"],
                    state.weather["humidity"],
                )
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print("[OWM] fetch error: %s" % exc)
            state.weather_error = str(exc)
            state.status_dirty = True
            state.weather_dirty = True
            gc.collect()
            await asyncio.sleep_ms(retry_ms)
            continue

        await asyncio.sleep_ms(0)  # yield between blocking HTTP calls

        try:
            forecast, trend = weather_svc.fetch_forecast_bundle()
            gc.collect()  # free parsed JSON payloads
            state.forecast = forecast
            state.weather_trend = trend
            state.forecast_dirty = True
            state.last_forecast_fetch_ms = ticks_ms()
        except (OSError, ValueError, RuntimeError) as exc:
            print("[OWM] forecast error: %s" % exc)
            gc.collect()

        await asyncio.sleep_ms(refresh_ms)


async def solar_task(state, solar_svc, cfg):
    if not solar_svc.enabled():
        print("[Solar] Disabled in config")
        return

    refresh_ms = int(cfg["solar"].get("refresh_ms", 300_000))

    while True:
        if state.wifi_online:
            try:
                data = solar_svc.fetch_realtime()
                gc.collect()
                if data is not None:
                    state.solar = data
                    state.solar_dirty = True
                    state.status_dirty = True
                    state.last_solar_fetch_ms = data["fetched_ms"]
                    print(
                        "[Solar] %.0fW gen %.0fW grid bat %.0f%%"
                        % (
                            state.solar["generation_w"],
                            state.solar["grid_w"],
                            state.solar["battery_soc"],
                        )
                    )
            except (OSError, ValueError, RuntimeError) as exc:
                print("[Solar] fetch error: %s" % exc)
                gc.collect()

        await asyncio.sleep_ms(refresh_ms)


async def metrics_task(state, metrics_svc, cfg):
    if not metrics_svc.enabled():
        print("[PC] Disabled in config")
        return

    refresh_ms = int(cfg["metrics"].get("refresh_ms", 10_000))

    _consec_fail = 0

    while True:
        if state.wifi_online:
            try:
                metrics = metrics_svc.fetch()
                gc.collect()
                state.metrics = metrics
                state.metrics_dirty = True
                state.status_dirty = True
                state.last_metrics_fetch_ms = metrics["fetched_ms"]
                print(
                    "[PC] CPU %.0f%% RAM %.0f%% DISK %.0f%%"
                    % (
                        state.metrics["cpu_pct"],
                        state.metrics["ram_pct"],
                        state.metrics["disk_pct"],
                    )
                )
                _consec_fail = 0
                await asyncio.sleep_ms(refresh_ms)
            except (OSError, ValueError, RuntimeError) as exc:
                print("[PC] fetch error: %s" % exc)
                gc.collect()
                _consec_fail += 1
                sleep_ms = min(refresh_ms * (2 ** min(_consec_fail, 5)), 300_000)
                print("[PC] backoff %ds after %d consecutive failures" % (sleep_ms // 1000, _consec_fail))
                await asyncio.sleep_ms(sleep_ms)
        else:
            await asyncio.sleep_ms(refresh_ms)


async def render_task(state, display, time_svc, cfg):
    stale_ms = int(cfg["weather"].get("stale_ms", 1_800_000))
    clock_ms = int(cfg["time"].get("clock_refresh_ms", 1_000))
    poll_ms = min(100, clock_ms)  # cap at 100 ms for snappy page transitions

    while True:
        now_local = None
        if state.time_synced:
            try:
                now_local = time_svc.now_localtime()
            except OSError:
                now_local = None

        if display.ready:
            try:
                display.render(state, now_local, stale_ms, ticks_ms())
            except MemoryError:
                print("[DISP] render MemoryError; skipping frame")
                gc.collect()

        await asyncio.sleep_ms(poll_ms)


async def web_config_task(state, cfg, wifi_svc, server_sock=None):
    web_cfg = cfg.get("web", {})
    web_enabled = bool(web_cfg.get("enabled", False))
    if not web_enabled:
        print("[WEB] Disabled in config")
        return

    try:
        from services.web_config_service import WebConfigService

        web_svc = WebConfigService(cfg, "config.json")
    except MemoryError:
        gc.collect()
        print("[WEB] service load MemoryError; disabled")
        return
    except Exception as exc:
        print("[WEB] service load error: %s" % exc)
        return

    if not web_svc.enabled():
        print("[WEB] Disabled in config")
        return

    port = web_svc.port()
    if port <= 0:
        port = 80

    try:
        import usocket as socket
    except ImportError:
        import socket

    max_req_bytes = 2048

    def _send_response(client, status, content_type, body):
        if not isinstance(body, str):
            body = str(body)
        payload = body.encode("utf-8")
        header = (
            "HTTP/1.1 %s\r\n"
            "Content-Type: %s\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n"
        ) % (status, content_type, len(payload))
        packet = header.encode("utf-8") + payload
        sendall = getattr(client, "sendall", None)
        if sendall is not None:
            sendall(packet)
        else:
            client.send(packet)

    def _is_timeout_error(exc):
        if len(exc.args) > 0:
            code = exc.args[0]
            if code in (110, 116):
                return True
            if isinstance(code, str):
                low = code.lower()
                if "timed out" in low or "etimedout" in low:
                    return True
        if len(exc.args) > 1 and isinstance(exc.args[1], str):
            low = exc.args[1].lower()
            if "timed out" in low or "etimedout" in low:
                return True
        text = str(exc).lower()
        if "timed out" in text or "etimedout" in text:
            return True
        return False

    def _read_http_request(client):
        buf = bytearray()
        header_end = -1

        while len(buf) < max_req_bytes:
            chunk = client.recv(min(512, max_req_bytes - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
            header_end = bytes(buf).find(b"\r\n\r\n")
            if header_end >= 0:
                break

        if header_end < 0:
            return None, None, 0, b""

        head = bytes(buf[:header_end]).decode("utf-8", "ignore")
        lines = head.split("\r\n")
        if not lines:
            return None, None, 0, b""

        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None, None, 0, b""

        method = parts[0]
        path = parts[1]
        content_length = 0

        for line in lines[1:]:
            low = line.lower()
            if low.startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except Exception:
                    content_length = 0
                break

        if content_length < 0:
            content_length = 0

        body_start = header_end + 4
        body = bytes(buf[body_start:])

        if method == "POST" and content_length > len(body):
            while len(body) < content_length and len(buf) < max_req_bytes:
                remaining_by_len = content_length - len(body)
                remaining_by_cap = max_req_bytes - len(buf)
                to_read = min(512, remaining_by_len, remaining_by_cap)
                if to_read <= 0:
                    break
                chunk = client.recv(to_read)
                if not chunk:
                    break
                buf.extend(chunk)
                body += chunk

        return method, path, content_length, body

    retry_ms = 10_000

    while True:
        try:
            if server_sock is None:
                while server_sock is None:
                    if not state.wifi_online:
                        await asyncio.sleep_ms(retry_ms)
                        continue

                    try:
                        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        try:
                            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        except Exception:
                            pass
                        server_sock.bind(("0.0.0.0", port))
                        server_sock.listen(1)
                        server_sock.settimeout(1)
                        state.web_ready = True
                    except OSError as exc:
                        state.web_ready = False
                        print("[WEB] bind/listen error: %s (retry in %ds)" % (exc, retry_ms // 1000))
                        if server_sock is not None:
                            try:
                                server_sock.close()
                            except Exception:
                                pass
                            server_sock = None
                        gc.collect()
                        await asyncio.sleep_ms(retry_ms)
            else:
                state.web_ready = True

            print("[WEB] Config UI: http://%s:%d/" % (wifi_svc.ip() or "0.0.0.0", port))

            while True:
                if not state.wifi_online:
                    await asyncio.sleep_ms(20)
                    continue

                client = None
                try:
                    client, _addr = server_sock.accept()
                except OSError as exc:
                    if _is_timeout_error(exc):
                        await asyncio.sleep_ms(20)
                        continue
                    print("[WEB] accept error: %s" % exc)
                    await asyncio.sleep_ms(20)
                    continue

                try:
                    try:
                        client.settimeout(1)
                    except Exception:
                        pass

                    method, path, content_length, body_bytes = _read_http_request(client)
                    if not method or not path:
                        _send_response(client, "400 Bad Request", "text/plain", "Bad request")
                        continue

                    if method == "GET" and path == "/":
                        ip = wifi_svc.ip()
                        html = web_svc.render_html(ip, cfg.get("ui", {}), cfg.get("metrics", {}))
                        _send_response(client, "200 OK", "text/html; charset=utf-8", html)
                        continue

                    if method == "POST" and path == "/save":
                        if content_length > max_req_bytes:
                            _send_response(client, "413 Payload Too Large", "text/plain", "Form too large")
                            continue

                        body = body_bytes.decode("utf-8", "ignore") if content_length > 0 else ""

                        form = web_svc.parse_form(body)
                        updated = web_svc.apply_form(form)

                        state.enabled_pages = _normalize_enabled_pages(updated.get("enabled_pages", []))
                        if state.page not in state.enabled_pages:
                            state.page = state.enabled_pages[0]
                            state.page_dirty = True

                        state.theme_name = _apply_theme_safe(updated.get("theme", "retro"))
                        state.mark_all_dirty()
                        print("[WEB] Config updated via HTTP")

                        ip = wifi_svc.ip()
                        html = web_svc.render_html(ip, cfg.get("ui", {}), cfg.get("metrics", {}))
                        _send_response(client, "200 OK", "text/html; charset=utf-8", html)
                        continue

                    _send_response(client, "404 Not Found", "text/plain", "Not found")
                except Exception as exc:
                    print("[WEB] request error: %s" % exc)
                finally:
                    if client is not None:
                        try:
                            client.close()
                        except Exception:
                            pass
                    gc.collect()
        except Exception as exc:
            print("[WEB] task error: %s" % exc)
            gc.collect()
            await asyncio.sleep_ms(retry_ms)
        finally:
            if server_sock is not None:
                try:
                    server_sock.close()
                except Exception:
                    pass
                server_sock = None
            if web_enabled:
                state.web_ready = False


async def memory_log_task():
    while True:
        gc.collect()
        print("[MEM] Free heap: %d bytes" % mem_free())
        await asyncio.sleep(60)


async def esp_status_task(state, wifi_svc):
    """Collect ESP32 system resource stats every 10 seconds."""
    refresh_ms = 10_000

    while True:
        gc.collect()
        status = {}

        # RAM
        free = mem_free()
        status["ram_free_kb"] = free // 1024 if free >= 0 else 0
        alloc = mem_alloc()
        status["ram_used_kb"] = alloc // 1024 if alloc >= 0 else -1

        # CPU frequency
        try:
            _machine = __import__("machine")
            status["cpu_mhz"] = _machine.freq() // 1_000_000
        except Exception:
            status["cpu_mhz"] = 0

        # Flash size
        try:
            _esp = __import__("esp")
            status["flash_kb"] = _esp.flash_size() // 1024
        except Exception:
            status["flash_kb"] = 0

        # Filesystem stats
        try:
            _uos = __import__("uos")
            sv = _uos.statvfs("/")
            status["fs_total_kb"] = (sv[0] * sv[2]) // 1024
            status["fs_free_kb"] = (sv[0] * sv[3]) // 1024
        except Exception:
            status["fs_total_kb"] = 0
            status["fs_free_kb"] = 0

        # WiFi: IP, RSSI, channel
        try:
            wlan = wifi_svc._wlan
            if wlan and wlan.isconnected():
                status["ip"] = wifi_svc.ip()
                status["rssi"] = wlan.status("rssi")
                try:
                    status["channel"] = wlan.config("channel")
                except Exception:
                    status["channel"] = 0
            else:
                status["ip"] = "offline"
                status["rssi"] = 0
                status["channel"] = 0
        except Exception:
            status["ip"] = "?"
            status["rssi"] = 0
            status["channel"] = 0

        # Uptime from ticks_ms (wraps ~25 days; good enough for display)
        status["uptime_s"] = ticks_ms() // 1000

        state.esp_status = status
        state.esp_status_dirty = True
        print("[ESP] RAM %dkB free  CPU %dMHz" % (status["ram_free_kb"], status["cpu_mhz"]))
        await asyncio.sleep_ms(refresh_ms)


async def app_main():
    print("\n================================================")
    print("  Retro Weather Clock MicroPython")
    print("================================================\n")

    try:
        cfg = load_config("config.json")
    except ConfigNotReadyError as exc:
        print("[CFG] %s" % exc)
        from ui.display_manager import DisplayManager
        display = DisplayManager()
        display.init()
        display.draw_config_error(str(exc))
        while True:
            await asyncio.sleep(5)

    _wifi_prealloc_ok = False
    try:
        import network as _net_pre

        _wlan_pre = _net_pre.WLAN(_net_pre.STA_IF)
        _wifi_prealloc_ok = bool(_wlan_pre.active())
        print(
            "[WiFi] prealloc status: %s"
            % ("active" if _wifi_prealloc_ok else "inactive")
        )
        del _wlan_pre, _net_pre
    except Exception:
        print("[WiFi] prealloc status: inactive")

    # Maximise contiguous free heap before the WiFi driver allocates its
    # ~16 KB RX-buffer pool.  The AppState + config dicts fragment the heap
    # enough to trigger "WiFi Out of Memory" if this is omitted.
    gc.collect()

    wifi_svc = WifiService(cfg, preallocated=_wifi_prealloc_ok)
    time_svc = TimeService(cfg)

    # Initial connectivity attempts.
    startup_retries = int(cfg["wifi"].get("startup_retries", 4))
    startup_retry_ms = int(cfg["wifi"].get("startup_retry_ms", 2_000))
    online = False
    for attempt in range(startup_retries):
        print("[WiFi] startup connect attempt %d/%d" % (attempt + 1, startup_retries))
        online = await wifi_svc.ensure_connected()
        if online:
            break
        if attempt < startup_retries - 1:
            await asyncio.sleep_ms(startup_retry_ms)

    synced = await time_svc.sync_ntp() if online else False

    # --- Startup weather bootstrap (before display init to avoid -202/-203) ---
    startup_weather = None
    startup_forecast = None
    startup_trend = None

    if online and bool(cfg["weather"].get("enabled", True)):
        _boot_heap = gc.mem_free()
        if _boot_heap < 70_000:
            print("[OWM] startup bootstrap skipped (strict memory guard)")
        else:
            from services.weather_service import WeatherService as _BootWX
            _wx = _BootWX(cfg)
            for _attempt in range(3):
                print("[OWM] startup fetch attempt %d/3" % (_attempt + 1))
                try:
                    startup_weather = _wx.fetch_current()
                    gc.collect()
                    print(
                        "[OWM] startup fetch OK: %.1fC %s"
                        % (startup_weather["temp_c"], startup_weather["condition"])
                    )
                    break
                except Exception as _exc:
                    print("[OWM] startup fetch error: %s" % _exc)
                    gc.collect()
            if startup_weather is not None:
                _heap2 = gc.mem_free()
                if _heap2 < 55_000:
                    print("[OWM] startup forecast skipped (memory guard)")
                else:
                    try:
                        startup_forecast, startup_trend = _wx.fetch_forecast_bundle()
                        gc.collect()
                    except Exception as _exc:
                        print("[OWM] startup forecast error: %s" % _exc)
                        gc.collect()
            del _wx, _BootWX
            gc.collect()

    # ---- Web socket pre-bind (provisioning window) -------------------------
    # Bind BEFORE any tasks start to avoid ENOBUFS from concurrent outbound
    # HTTP connections exhausting the lwIP PCB pool.
    _web_server_sock = None
    _web_port = int(cfg.get("web", {}).get("port", 80))
    if bool(cfg.get("web", {}).get("enabled", True)) and online:
        try:
            try:
                import usocket as _ws
            except ImportError:
                import socket as _ws
            _web_server_sock = _ws.socket(_ws.AF_INET, _ws.SOCK_STREAM)
            try:
                _web_server_sock.setsockopt(_ws.SOL_SOCKET, _ws.SO_REUSEADDR, 1)
            except Exception:
                pass
            _web_server_sock.bind(("0.0.0.0", _web_port))
            _web_server_sock.listen(1)
            _web_server_sock.settimeout(1)
            print("[WEB] Pre-bound port %d" % _web_port)
            del _ws
        except OSError as exc:
            print("[WEB] Pre-bind failed: %s — will retry in task" % exc)
            if _web_server_sock is not None:
                try:
                    _web_server_sock.close()
                except Exception:
                    pass
                _web_server_sock = None
        gc.collect()
    # -----------------------------------------------------------------------
    from ui.display_manager import DisplayManager
    display = DisplayManager()
    display.init()
    display.draw_boot("Booting...")

    state = AppState()
    state.enabled_pages = _normalize_enabled_pages(cfg.get("ui", {}).get("enabled_pages", []))
    state.theme_name = _apply_theme_safe(cfg.get("ui", {}).get("theme", "retro"))
    if state.page not in state.enabled_pages:
        state.page = state.enabled_pages[0]
        state.page_dirty = True
    state.metrics_stale_ms = int(cfg.get("metrics", {}).get("stale_ms", 120_000))
    state.wifi_online = online
    state.time_synced = synced
    state.web_ready = _web_server_sock is not None or not bool(cfg.get("web", {}).get("enabled", True))

    if startup_weather is not None:
        state.weather = startup_weather
        state.weather_dirty = True
        state.status_dirty = True
        state.last_weather_fetch_ms = startup_weather["fetched_ms"]

    if startup_forecast is not None:
        state.forecast = startup_forecast
        state.weather_trend = startup_trend if startup_trend is not None else []
        state.forecast_dirty = True
        state.last_forecast_fetch_ms = ticks_ms()

    gc.collect()
    from services.weather_service import WeatherService
    from services.solar_service import SolarService
    from services.metrics_service import MetricsService

    weather_svc = WeatherService(cfg)
    solar_svc = SolarService(cfg)
    metrics_svc = MetricsService(cfg)
    gc.collect()

    tasks = [
        asyncio.create_task(button_task(state)),
        asyncio.create_task(wifi_task(state, wifi_svc, cfg, time_svc)),
        asyncio.create_task(render_task(state, display, time_svc, cfg)),
        asyncio.create_task(web_config_task(state, cfg, wifi_svc, _web_server_sock)),
        asyncio.create_task(memory_log_task()),
        asyncio.create_task(esp_status_task(state, wifi_svc)),
        asyncio.create_task(weather_task(state, weather_svc, cfg)),
        asyncio.create_task(solar_task(state, solar_svc, cfg)),
        asyncio.create_task(metrics_task(state, metrics_svc, cfg)),
    ]

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()


try:
    asyncio.run(app_main())
finally:
    asyncio.new_event_loop()
