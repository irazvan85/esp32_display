"""Retro Weather Clock for MicroPython (initial implementation).

This is phase-1/2 migration code: hardware, config persistence, async scheduler,
and service wiring are implemented. Rendering and service behavior are intentionally
kept simple while parity work continues.
"""

import gc

try:
    json = __import__("ujson")
except ImportError:
    import json

try:
    asyncio = __import__("uasyncio")
except ImportError:
    import asyncio

try:
    urandom = __import__("urandom")
except ImportError:
    urandom = None

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


def _get_display_manager_class():
    from ui.display_manager import DisplayManager
    return DisplayManager


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


def _is_transport_error(exc):
    code = None
    if len(exc.args) > 0:
        code = exc.args[0]
    if code in (-203, -202, 118, 113):
        return True

    text = str(exc).lower()
    if "ehostunreach" in text:
        return True
    if "host unreachable" in text:
        return True
    if "enetunreach" in text:
        return True
    if "network unreachable" in text:
        return True
    return False


def _is_enobufs(exc):
    if len(exc.args) > 0 and exc.args[0] == 105:
        return True
    text = str(exc).lower()
    if "enobufs" in text or "no buffer" in text:
        return True
    return False


def _schedule_transport_reconnect(state, cfg):
    threshold = int(cfg["wifi"].get("transport_error_reconnect_threshold", 3))
    if threshold < 1:
        threshold = 1

    if state.net_error_streak >= threshold and not state.force_wifi_reconnect:
        state.force_wifi_reconnect = True
        print("[WiFi] scheduling reconnect after transport errors")


def _assoc_fail_state_cfg(cfg):
    wifi_cfg = cfg.get("wifi", {}) if isinstance(cfg, dict) else {}

    enabled = bool(wifi_cfg.get("assoc_fail_state_enabled", False))
    path = wifi_cfg.get("assoc_fail_state_path", "wifi_assoc_fail_state.json")
    if not isinstance(path, str) or not path:
        path = "wifi_assoc_fail_state.json"

    return enabled, path


def _assoc_fail_state_load_count(cfg):
    enabled, path = _assoc_fail_state_cfg(cfg)
    if not enabled:
        return 0

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError:
        return 0
    except Exception as exc:
        print("[WiFi] ASSOC_FAIL state load error: %s" % exc)
        return 0

    if not isinstance(payload, dict):
        return 0

    try:
        count = int(payload.get("assoc_fail_count", 0))
    except Exception:
        return 0

    if count < 0:
        return 0
    if count > 1000:
        return 1000
    return count


def _assoc_fail_state_save_count(cfg, count):
    enabled, path = _assoc_fail_state_cfg(cfg)
    if not enabled:
        return False

    try:
        count = int(count)
    except Exception:
        count = 0

    if count < 0:
        count = 0
    if count > 1000:
        count = 1000

    payload = {
        "v": 1,
        "assoc_fail_count": count,
    }

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return True
    except Exception as exc:
        print("[WiFi] ASSOC_FAIL state save error: %s" % exc)
        return False


def _assoc_fail_state_clear(cfg):
    _assoc_fail_state_save_count(cfg, 0)


def _rand_bounded(max_value):
    if max_value <= 0:
        return 0

    if urandom is not None:
        try:
            return int(urandom.getrandbits(30) % (max_value + 1))
        except Exception:
            pass

    return ticks_ms() % (max_value + 1)


def _with_backoff_jitter(base_ms, jitter_pct):
    if jitter_pct <= 0:
        return base_ms

    span_ms = (base_ms * jitter_pct) // 100
    if span_ms <= 0:
        return base_ms

    delta_ms = _rand_bounded(span_ms * 2) - span_ms
    jittered_ms = base_ms + delta_ms
    if jittered_ms < 5_000:
        return 5_000
    return jittered_ms


def _assoc_fail_backoff_ms(assoc_fail_count, cfg):
    wifi_cfg = cfg.get("wifi", {})

    jitter_pct = int(wifi_cfg.get("assoc_fail_backoff_jitter_pct", 0))
    if jitter_pct < 0:
        jitter_pct = 0
    if jitter_pct > 30:
        jitter_pct = 30

    backoff_cap_s = int(wifi_cfg.get("assoc_fail_backoff_max_s", 360))
    if backoff_cap_s < 120:
        backoff_cap_s = 120

    long_after_n = int(wifi_cfg.get("assoc_fail_long_cooldown_after_n", 3))
    if long_after_n < 2:
        long_after_n = 2

    long_cooldown_s = int(wifi_cfg.get("assoc_fail_long_cooldown_s", 720))
    if long_cooldown_s < backoff_cap_s:
        long_cooldown_s = backoff_cap_s

    stepped_s = min(120 * assoc_fail_count, backoff_cap_s)
    if assoc_fail_count >= long_after_n:
        base_ms = long_cooldown_s * 1000
        return _with_backoff_jitter(base_ms, jitter_pct), True

    base_ms = stepped_s * 1000
    return _with_backoff_jitter(base_ms, jitter_pct), False


async def _handle_assoc_fail_backoff(state, wifi_svc, cfg, assoc_fail_count):
    backoff_ms, is_long = _assoc_fail_backoff_ms(assoc_fail_count, cfg)
    if is_long:
        print(
            "[WiFi] ASSOC_FAIL #%d - radio off, quiet window %ds (next retry in %ds)"
            % (assoc_fail_count, backoff_ms // 1000, backoff_ms // 1000)
        )
    else:
        print(
            "[WiFi] ASSOC_FAIL #%d - radio off, backoff %ds (next retry in %ds)"
            % (assoc_fail_count, backoff_ms // 1000, backoff_ms // 1000)
        )
    state.status_dirty = True
    wifi_svc.radio_off()
    await asyncio.sleep_ms(backoff_ms)


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


async def wifi_task(
    state,
    wifi_svc,
    cfg,
    time_svc,
    initial_assoc_fail_count=0,
    initial_assoc_fail_pending=False,
):
    wifi_check_ms = int(cfg["wifi"].get("check_interval_ms", 30_000))
    wifi_offline_retry_ms = int(cfg["wifi"].get("offline_retry_ms", 5_000))
    ntp_retry_ms = int(cfg.get("time", {}).get("ntp_retry_ms", 60_000))
    if ntp_retry_ms < 5_000:
        ntp_retry_ms = 5_000

    _reconnect_backoff_ms = wifi_offline_retry_ms
    _assoc_fail_count = initial_assoc_fail_count if initial_assoc_fail_count > 0 else 0
    _assoc_fail_pending = bool(initial_assoc_fail_pending)
    _ntp_retry_wait_ms = 0

    while True:
        if _assoc_fail_pending and not state.wifi_online:
            print("[WiFi] persisted ASSOC_FAIL: startup probe attempt")
            # Step 1: wifitest-style direct probe (no scan pinning).
            probe_online = await wifi_svc.ensure_connected(allow_scan_retry=False)
            if (not probe_online) and wifi_svc.assoc_fail:
                # Step 2: force a full STA reset path before entering long backoff.
                print("[WiFi] persisted ASSOC_FAIL: recovery probe with STA reset")
                probe_online = await wifi_svc.ensure_connected(force=True, allow_scan_retry=True)
            if probe_online:
                _assoc_fail_pending = False
                _assoc_fail_count = 0
                _assoc_fail_state_clear(cfg)
                changed = probe_online != state.wifi_online
                state.wifi_online = probe_online
                state.net_error_streak = 0
                if changed:
                    state.status_dirty = True
                    state.page_dirty = True
                if not state.time_synced:
                    state.time_synced = await time_svc.sync_ntp()
                    if state.time_synced:
                        _ntp_retry_wait_ms = 0
                    else:
                        _ntp_retry_wait_ms = ntp_retry_ms
                continue

            _assoc_fail_pending = False
            if wifi_svc.assoc_fail:
                _assoc_fail_state_save_count(cfg, _assoc_fail_count)
                await _handle_assoc_fail_backoff(state, wifi_svc, cfg, _assoc_fail_count)
            else:
                print("[WiFi] persisted ASSOC_FAIL probe timed out; resuming normal retry policy")
                await asyncio.sleep_ms(wifi_offline_retry_ms)
            continue

        force_requested = state.force_wifi_reconnect
        online = await wifi_svc.ensure_connected(force=force_requested)
        changed = online != state.wifi_online
        state.wifi_online = online

        if force_requested:
            # Always clear the flag so we don't hammer the AP on every cycle.
            state.force_wifi_reconnect = False
            if online:
                state.net_error_streak = 0
                _reconnect_backoff_ms = wifi_offline_retry_ms
                _assoc_fail_count = 0
                _assoc_fail_state_clear(cfg)
                print("[WiFi] reconnect healed transport path")
            else:
                _ntp_retry_wait_ms = 0
                if wifi_svc.assoc_fail:
                    _assoc_fail_count += 1
                    _assoc_fail_state_save_count(cfg, _assoc_fail_count)
                    await _handle_assoc_fail_backoff(state, wifi_svc, cfg, _assoc_fail_count)
                    continue
                _reconnect_backoff_ms = min(_reconnect_backoff_ms * 2, 120_000)
                print("[WiFi] reconnect failed — backoff %ds" % (_reconnect_backoff_ms // 1000))
                state.status_dirty = True
                await asyncio.sleep_ms(_reconnect_backoff_ms)
                continue

        if changed:
            _reconnect_backoff_ms = wifi_offline_retry_ms
            state.status_dirty = True
            state.page_dirty = True
            if online:
                _assoc_fail_count = 0
                _assoc_fail_state_clear(cfg)
                state.time_synced = await time_svc.sync_ntp()
                if state.time_synced:
                    _ntp_retry_wait_ms = 0
                else:
                    _ntp_retry_wait_ms = ntp_retry_ms
            else:
                _ntp_retry_wait_ms = 0

        if online and (not state.time_synced) and _ntp_retry_wait_ms <= 0:
            state.time_synced = await time_svc.sync_ntp()
            if state.time_synced:
                _ntp_retry_wait_ms = 0
                state.status_dirty = True
            else:
                _ntp_retry_wait_ms = ntp_retry_ms

        if online:
            sleep_ms = wifi_check_ms
            if (not state.time_synced) and _ntp_retry_wait_ms > 0 and _ntp_retry_wait_ms < sleep_ms:
                sleep_ms = _ntp_retry_wait_ms

            await asyncio.sleep_ms(sleep_ms)

            if (not state.time_synced) and _ntp_retry_wait_ms > 0:
                _ntp_retry_wait_ms -= sleep_ms
                if _ntp_retry_wait_ms < 0:
                    _ntp_retry_wait_ms = 0
        elif wifi_svc.assoc_fail:
            _ntp_retry_wait_ms = 0
            _assoc_fail_count += 1
            _assoc_fail_state_save_count(cfg, _assoc_fail_count)
            await _handle_assoc_fail_backoff(state, wifi_svc, cfg, _assoc_fail_count)
        else:
            _ntp_retry_wait_ms = 0
            await asyncio.sleep_ms(wifi_offline_retry_ms)


async def weather_task(state, weather_svc, cache_svc, cfg):
    if not bool(cfg["weather"].get("enabled", True)):
        print("[OWM] Disabled in config")
        return

    refresh_ms = int(cfg["weather"].get("refresh_ms", 600_000))
    retry_ms = int(cfg["weather"].get("retry_ms", 30_000))
    offline_retry_ms = int(cfg["weather"].get("offline_retry_ms", 5_000))

    # Escalating DNS failure backoff: 10s → 30s → 60s → 120s cap
    _dns_fail_streak = 0
    _DNS_RETRY_SCHEDULE = (10_000, 30_000, 60_000, 120_000)

    # If startup bootstrap already seeded weather, defer the first periodic
    # fetch to avoid a back-to-back HTTP+parse that exhausts fragmented heap.
    if state.last_weather_fetch_ms and state.last_forecast_fetch_ms:
        print("[OWM] bootstrap data present, deferring first fetch by refresh_ms")
        await asyncio.sleep_ms(refresh_ms)

    while True:
        if not state.wifi_online:
            await asyncio.sleep_ms(offline_retry_ms)
            continue

        gc.collect()
        try:
            weather = weather_svc.fetch_current()
            gc.collect()  # free parsed JSON payloads

            state.weather = weather
            state.weather_dirty = True
            state.status_dirty = True
            state.weather_error = ""
            state.net_error_streak = 0
            _dns_fail_streak = 0

            state.last_weather_fetch_ms = weather["fetched_ms"]

            try:
                cache_svc.save(
                    weather=state.weather,
                    forecast=state.forecast,
                    trend=state.weather_trend,
                )
            except Exception as exc:
                print("[OWM] cache save error: %s" % exc)

            print(
                "[OWM] %.1fC %s Hum:%d%%"
                % (
                    state.weather["temp_c"],
                    state.weather["condition"],
                    state.weather["humidity"],
                )
            )
        except (OSError, ValueError, RuntimeError) as exc:
            _err_code = exc.args[0] if isinstance(exc, OSError) and exc.args else None
            _is_dns_err = _err_code in (-202, -203)

            if _is_dns_err:
                _dns_fail_streak += 1
                _retry_idx = min(_dns_fail_streak - 1, len(_DNS_RETRY_SCHEDULE) - 1)
                _owm_retry_ms = _DNS_RETRY_SCHEDULE[_retry_idx]
                if _err_code == -203:
                    print(
                        "[OWM] DNS memory error (EAI_MEMORY) streak=%d — retry in %ds"
                        % (_dns_fail_streak, _owm_retry_ms // 1000)
                    )
                else:
                    print(
                        "[OWM] DNS failure (EAI_FAIL) streak=%d — retry in %ds"
                        % (_dns_fail_streak, _owm_retry_ms // 1000)
                    )
                # After 5 consecutive DNS failures the WiFi stack / DNS resolver
                # is likely stuck; force a full WiFi reconnect to flush the state.
                if _dns_fail_streak >= 5 and not state.force_wifi_reconnect:
                    print("[OWM] DNS stuck — requesting WiFi reconnect to flush DNS")
                    state.force_wifi_reconnect = True
                    _dns_fail_streak = 0
            else:
                _owm_retry_ms = retry_ms
                print("[OWM] fetch error: %s" % exc)

            state.weather_error = str(exc)
            state.status_dirty = True
            state.weather_dirty = True

            if _is_transport_error(exc) and not _is_enobufs(exc):
                state.net_error_streak += 1
                _schedule_transport_reconnect(state, cfg)

            gc.collect()
            await asyncio.sleep_ms(_owm_retry_ms)
            continue

        await asyncio.sleep_ms(0)  # yield between blocking HTTP calls

        try:
            forecast, trend = weather_svc.fetch_forecast_bundle()
            gc.collect()  # free parsed JSON payloads
            state.forecast = forecast
            state.weather_trend = trend
            state.forecast_dirty = True
            state.last_forecast_fetch_ms = ticks_ms()

            try:
                cache_svc.save(
                    weather=state.weather,
                    forecast=state.forecast,
                    trend=state.weather_trend,
                )
            except Exception as exc:
                print("[OWM] cache save error: %s" % exc)
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
                state.net_error_streak = 0
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
                if _is_transport_error(exc) and not _is_enobufs(exc):
                    state.net_error_streak += 1
                    _schedule_transport_reconnect(state, cfg)
                if _consec_fail in (1, 3, 6):
                    try:
                        diag = metrics_svc.connectivity_diag()
                        host = diag.get("host") or "?"
                        port = diag.get("port") or 80
                        print("[PCDBG] endpoint %s:%d" % (host, port))
                        if diag.get("ifconfig") is not None:
                            print("[PCDBG] ifconfig %s" % (diag.get("ifconfig"),))
                        if diag.get("resolve_ok"):
                            print("[PCDBG] resolve ok %s" % (diag.get("resolve_addr"),))
                        else:
                            print("[PCDBG] resolve err %s" % diag.get("resolve_error"))
                        if diag.get("connect_ok"):
                            print("[PCDBG] connect ok")
                        else:
                            print("[PCDBG] connect err %s" % diag.get("connect_error"))
                    except Exception as dbg_exc:
                        print("[PCDBG] diag error: %s" % dbg_exc)
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

    retry_base_ms = 10_000
    retry_ms = retry_base_ms

    while True:
        try:
            if server_sock is None:
                while server_sock is None:
                    if not state.wifi_online:
                        await asyncio.sleep_ms(retry_ms)
                        continue

                    try:
                        gc.collect()
                        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        try:
                            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        except Exception:
                            pass
                        server_sock.bind(("0.0.0.0", port))
                        server_sock.listen(1)
                        server_sock.settimeout(1)
                        retry_ms = retry_base_ms
                        state.web_ready = True
                    except OSError as exc:
                        state.web_ready = False
                        if _is_enobufs(exc):
                            retry_ms = 300_000  # 5 min — PCB pool exhausted, wait it out
                            print("[WEB] ENOBUFS — PCB pool exhausted, retry in 5 min")
                        else:
                            retry_ms = min(retry_ms * 2, 120_000)
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


def _dns_prewarm(cfg):
    time_cfg = cfg.get("time", {}) if isinstance(cfg, dict) else {}
    host = time_cfg.get("ntp_server", "pool.ntp.org")
    if not isinstance(host, str) or not host:
        host = "pool.ntp.org"

    try:
        try:
            import usocket as _socket
        except ImportError:
            import socket as _socket

        _socket.getaddrinfo(host, 123)
        print("[WiFi] DNS pre-warm ok (%s)" % host)
        return True
    except OSError as exc:
        print("[WiFi] DNS pre-warm failed: %s" % exc)
        return False
    except Exception as exc:
        print("[WiFi] DNS pre-warm error: %s" % exc)
        return False
    finally:
        gc.collect()



async def app_main():
    print("\n================================================")
    print("  Retro Weather Clock MicroPython")
    print("================================================\n")

    try:
        cfg = load_config("config.json")
    except ConfigNotReadyError as exc:
        print("[CFG] %s" % exc)
        # Config error path: load DisplayManager here (WiFi not needed)
        gc.collect()
        DisplayManager = _get_display_manager_class()
        display = DisplayManager()
        display.init()
        display.draw_config_error(str(exc))
        while True:
            await asyncio.sleep(5)

    # Maximise contiguous free heap before the WiFi driver allocates its
    # ~16 KB RX-buffer pool.  The AppState + config dicts fragment the heap
    # enough to trigger "WiFi Out of Memory" if this is omitted.
    gc.collect()

    wifi_svc = WifiService(cfg, preallocated=False)
    time_svc = TimeService(cfg)

    # Initial connectivity attempts.
    startup_retries = int(cfg["wifi"].get("startup_retries", 4))
    startup_retry_ms = int(cfg["wifi"].get("startup_retry_ms", 2_000))
    startup_assoc_retry_ms = int(cfg["wifi"].get("assoc_fail_startup_quick_retry_ms", 15_000))
    if startup_assoc_retry_ms < 3_000:
        startup_assoc_retry_ms = 3_000
    online = False
    startup_assoc_fail_count = _assoc_fail_state_load_count(cfg)
    startup_assoc_fail_pending = startup_assoc_fail_count > 0
    startup_assoc_recovered = False
    if startup_assoc_fail_pending:
        print(
            "[WiFi] persisted ASSOC_FAIL #%d - probing once before wifi_task backoff"
            % startup_assoc_fail_count
        )
        print("[WiFi] persisted ASSOC_FAIL: early boot probe attempt")
        online = await wifi_svc.ensure_connected(allow_scan_retry=False)
        if online:
            startup_assoc_fail_count = 0
            startup_assoc_fail_pending = False
            startup_assoc_recovered = True
            _assoc_fail_state_clear(cfg)
            print("[WiFi] early boot probe connected; cleared persisted ASSOC_FAIL state")
        elif wifi_svc.assoc_fail:
            print("[WiFi] early boot probe still ASSOC_FAIL; deferring to wifi_task policy")
        else:
            startup_assoc_fail_pending = False
            print("[WiFi] early boot probe timed out; resuming normal retry policy")
    else:
        for attempt in range(startup_retries):
            print("[WiFi] startup connect attempt %d/%d" % (attempt + 1, startup_retries))
            # Allow BSSID scan retry on the final attempt — this is the last chance
            # before the firmware enters the long assoc_fail quiet window.
            is_last = (attempt == startup_retries - 1)
            online = await wifi_svc.ensure_connected(allow_scan_retry=is_last)
            if online:
                break
            if wifi_svc.assoc_fail:
                startup_assoc_fail_count += 1
                _assoc_fail_state_save_count(cfg, startup_assoc_fail_count)
                if attempt < startup_retries - 1:
                    print(
                        "[WiFi] startup ASSOC_FAIL #%d - quick cooldown %ds before retry"
                        % (startup_assoc_fail_count, startup_assoc_retry_ms // 1000)
                    )
                    wifi_svc.radio_off()
                    await asyncio.sleep_ms(startup_assoc_retry_ms)
                    continue

                startup_assoc_fail_pending = True
                print("[WiFi] startup ASSOC_FAIL detected - deferring retries to wifi_task policy")
                # Hard-reset now so the second boot has a clean heap.
                # Multiple ASSOC_FAIL retry cycles fragment DMA-capable RAM;
                # display.init() requires a contiguous ~12 KB DMA block and
                # will abort() the IDF if it can't allocate one.
                print("[WiFi] Hard reset to reclaim fragmented heap before display init")
                try:
                    import machine as _machine_rst
                    _machine_rst.reset()
                except Exception:
                    pass
                break
            if attempt < startup_retries - 1:
                await asyncio.sleep_ms(startup_retry_ms)

    if online:
        startup_assoc_fail_count = 0
        startup_assoc_fail_pending = False
        _assoc_fail_state_clear(cfg)

    # Aggressive GC after the startup WiFi loop.  Repeated ASSOC_FAIL cycles
    # can leave fragmented allocations that prevent the SPI DMA buffer allocation
    # in display.init() from finding a contiguous free block.
    gc.collect()

    # Load DisplayManager after WiFi is up. WiFi connect() and active() both
    # require internal IDF heap; loading display_manager.mpy before WiFi
    # connects fragments that region and causes "WiFi Out of Memory".
    # With .mpy pre-compiled bytecode, this import avoids a compile-time heap
    # spike and fits comfortably in the ~100 KB remaining after WiFi.
    DisplayManager = _get_display_manager_class()
    gc.collect()

    synced = await time_svc.sync_ntp() if online else False

    gc.collect()
    # --- Startup weather bootstrap (BEFORE display init) ---
    # OWM bootstrap must happen before DisplayManager.init() because
    # DisplayManager's SPI DMA buffers and lwIP's MEMP_NETDB DNS allocator
    # both draw from the same DMA-capable internal RAM region on ESP32.
    # After display init, DNS queries fail with EAI_MEMORY (-203) because
    # there is no DMA-capable contiguous block left for the DNS query struct.
    # Doing OWM first avoids this: DNS succeeds while DMA memory is plentiful,
    # and display init runs after gc.collect() frees the OWM transient allocs.
    startup_weather = None
    startup_forecast = None
    startup_trend = None
    _bootstrap_dns_failed = False

    weather_enabled = bool(cfg["weather"].get("enabled", True))
    startup_bootstrap_enabled = bool(cfg["weather"].get("startup_bootstrap", False))
    if startup_assoc_recovered and startup_bootstrap_enabled:
        print("[OWM] startup bootstrap skipped after ASSOC_FAIL recovery")
        startup_bootstrap_enabled = False

    if online and (not weather_enabled or not startup_bootstrap_enabled):
        if not _dns_prewarm(cfg):
            _bootstrap_dns_failed = True

    if online and weather_enabled and startup_bootstrap_enabled:
        _boot_heap = gc.mem_free()
        if _boot_heap < 55_000:
            print("[OWM] startup bootstrap skipped — heap %d < 55000" % _boot_heap)
        else:
            gc.collect()
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
                except OSError as _exc:
                    _exc_code = _exc.args[0] if _exc.args else None
                    print("[OWM] startup fetch error: %s" % _exc)
                    gc.collect()
                    if _exc_code in (-202, -203):
                        print("[OWM] DNS stuck at bootstrap — skipping remaining attempts")
                        _bootstrap_dns_failed = True
                        break
                except Exception as _exc:
                    print("[OWM] startup fetch error: %s" % _exc)
                    gc.collect()
            del _wx, _BootWX
            gc.collect()
    elif online and weather_enabled and not startup_bootstrap_enabled:
        print("[OWM] startup bootstrap disabled (config)")

    # Init display AFTER OWM bootstrap. gc.collect() above freed the OWM
    # transient socket+JSON allocations (~7 KB). Display init then has access
    # to those freed blocks for its SPI DMA buffer allocation.
    display = DisplayManager()
    gc.collect()
    try:
        display.init()
    except MemoryError:
        gc.collect()
        display.init()
    display.draw_boot("Booting...")
    gc.collect()

    from services.weather_cache_service import WeatherCacheService

    weather_cache_svc = WeatherCacheService("weather_cache.json")
    if weather_enabled and startup_weather is None:
        print("[OWM] startup weather unavailable; trying cache")
        cached = weather_cache_svc.load()
        if cached is not None:
            cached_weather = cached.get("weather")
            if cached_weather is not None and bool(cached_weather.get("valid", False)):
                cached_weather["fetched_ms"] = ticks_ms()
                startup_weather = cached_weather
                startup_forecast = cached.get("forecast") or []
                startup_trend = cached.get("trend") or []
                print("[OWM] startup weather restored from cache")

        # If startup is offline and no valid cache exists, expose a clear
        # placeholder instead of a blank weather panel.
        if startup_weather is None and not online:
            startup_weather = {
                "valid": True,
                "temp_c": 0.0,
                "feels_like_c": 0.0,
                "humidity": 0,
                "condition": "Offline",
                "condition_id": 800,
                "wind_ms": 0.0,
                "fetched_ms": 0,
            }
            print("[OWM] startup weather fallback: offline placeholder")

    gc.collect()
    # reclaim heap before opening web listener socket

    # ---- Web socket pre-bind (provisioning window) -------------------------
    # Bind BEFORE any tasks start to avoid ENOBUFS from concurrent outbound
    # HTTP connections exhausting the lwIP PCB pool.
    _web_server_sock = None
    _web_port = int(cfg.get("web", {}).get("port", 80))
    if bool(cfg.get("web", {}).get("enabled", False)) and online:
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

    state = AppState()
    state.enabled_pages = _normalize_enabled_pages(cfg.get("ui", {}).get("enabled_pages", []))
    state.theme_name = _apply_theme_safe(cfg.get("ui", {}).get("theme", "retro"))
    if state.page not in state.enabled_pages:
        state.page = state.enabled_pages[0]
        state.page_dirty = True
    state.metrics_stale_ms = int(cfg.get("metrics", {}).get("stale_ms", 120_000))
    state.wifi_online = online
    state.time_synced = synced
    state.web_ready = _web_server_sock is not None or not bool(cfg.get("web", {}).get("enabled", False))
    try:
        _pc = __import__("ui.pixel_capture", None, None, ("setup",))
        _pc.setup(state, display, cfg)
    except Exception:
        pass

    # If bootstrap DNS failed, the lwIP DNS table is full/stuck.
    # Do NOT force-reconnect here: rapid re-association causes ASSOC_FAIL.
    # wifi_task's own offline_retry path will flush the lwIP stack on its
    # first reconnect cycle; weather_task retries with its own backoff.
    if _bootstrap_dns_failed:
        print("[OWM] Bootstrap DNS failed — weather_task will retry with backoff")

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

    try:
        from services.uart_capture_service import uart_capture_task as _uart_capture_task
        _uart_capture_enabled = True
    except ImportError:
        _uart_capture_enabled = False

    weather_svc = WeatherService(cfg)
    solar_svc = SolarService(cfg)
    metrics_svc = MetricsService(cfg)
    gc.collect()

    tasks = [
        asyncio.create_task(button_task(state)),
        asyncio.create_task(
            wifi_task(
                state,
                wifi_svc,
                cfg,
                time_svc,
                initial_assoc_fail_count=startup_assoc_fail_count,
                initial_assoc_fail_pending=startup_assoc_fail_pending,
            )
        ),
        asyncio.create_task(render_task(state, display, time_svc, cfg)),
        asyncio.create_task(web_config_task(state, cfg, wifi_svc, _web_server_sock)),
        asyncio.create_task(memory_log_task()),
        asyncio.create_task(esp_status_task(state, wifi_svc)),
        asyncio.create_task(weather_task(state, weather_svc, weather_cache_svc, cfg)),
        asyncio.create_task(solar_task(state, solar_svc, cfg)),
        asyncio.create_task(metrics_task(state, metrics_svc, cfg)),
    ]

    if _uart_capture_enabled:
        tasks.append(asyncio.create_task(_uart_capture_task(state)))

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(app_main())
    finally:
        asyncio.new_event_loop()
