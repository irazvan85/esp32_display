"""UART display-state snapshot and menu-control service.

Commands from PC over UART:
    !SNAP             -> emit JSON snapshot between SNAP markers
    !PAGE <n>         -> switch to enabled page <n>
    !NEXT             -> switch to next enabled page
    !SUBPAGE <0|1>    -> set metrics subpage (PC monitor)
    !HELP             -> print command usage marker

Usage on device (background async task - added in main.py):
    from services.uart_capture_service import uart_capture_task
    asyncio.create_task(uart_capture_task(state))
"""

import gc
import sys

try:
    asyncio = __import__("uasyncio")
except ImportError:
    import asyncio

try:
    uselect = __import__("uselect")
except ImportError:
    try:
        uselect = __import__("select")
    except ImportError:
        uselect = None

try:
    ujson = __import__("ujson")
except ImportError:
    import json as ujson

try:
    _time = __import__("time")
except ImportError:
    _time = None


_PAGE_MIN = 0
_PAGE_MAX = 5


def _safe(value, default=None):
    """Return value if JSON-serialisable, else default."""
    try:
        ujson.dumps(value)
        return value
    except (TypeError, ValueError, OverflowError):
        return default


def _local_time_value():
    if _time is None:
        return None
    localtime_fn = getattr(_time, "localtime", None)
    if localtime_fn is None:
        return None

    try:
        now_local = localtime_fn()
    except (AttributeError, OSError, TypeError, ValueError):
        return None

    if not now_local or len(now_local) < 7:
        return None

    try:
        return [
            int(now_local[0]),
            int(now_local[1]),
            int(now_local[2]),
            int(now_local[3]),
            int(now_local[4]),
            int(now_local[5]),
            int(now_local[6]),
        ]
    except (TypeError, ValueError, IndexError):
        return None


def _ticks_ms_value():
    if _time is None:
        return 0
    ticks_ms_fn = getattr(_time, "ticks_ms", None)
    if ticks_ms_fn is None:
        return 0
    try:
        return int(ticks_ms_fn())
    except (TypeError, ValueError, OSError):
        return 0


def _build_snapshot(state):
    """Build a serialisable dict from the current AppState."""
    return {
        "page": _safe(state.page, 0),
        "enabled_pages": _safe(state.enabled_pages, []),
        "metrics_subpage": int(getattr(state, "metrics_subpage", 0)),
        "display_capture": _safe(getattr(state, "display_capture", {}), {}),
        "local_time": _safe(_local_time_value(), None),
        "snapshot_ms": int(_ticks_ms_value()),
        "wifi_online": bool(state.wifi_online),
        "time_synced": bool(state.time_synced),
        "web_ready": bool(getattr(state, "web_ready", False)),
        "weather_error": _safe(state.weather_error, ""),
        "net_error_streak": int(getattr(state, "net_error_streak", 0)),
        "weather": _safe(state.weather, {}),
        "forecast": _safe(state.forecast, []),
        "weather_trend": _safe(state.weather_trend, []),
        "solar": _safe(state.solar, {}),
        "metrics": _safe(state.metrics, {}),
        "esp_status": _safe(state.esp_status, {}),
        "last_weather_fetch_ms": int(getattr(state, "last_weather_fetch_ms", 0)),
        "last_metrics_fetch_ms": int(getattr(state, "last_metrics_fetch_ms", 0)),
        "last_solar_fetch_ms": int(getattr(state, "last_solar_fetch_ms", 0)),
    }


def _emit_snapshot(state):
    gc.collect()
    try:
        data = _build_snapshot(state)
        print(">>SNAP_START")
        print(ujson.dumps(data))
        print(">>SNAP_END")
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        print(">>SNAP_ERR %s" % exc)
    gc.collect()


def _normalize_enabled_pages(value):
    if not isinstance(value, list):
        return [0, 1, 2, 3, 4, 5]

    pages = []
    for item in value:
        if not isinstance(item, int):
            continue
        if _PAGE_MIN <= item <= _PAGE_MAX and item not in pages:
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


def _set_true_flag(state, attr_name):
    try:
        setattr(state, attr_name, True)
    except (AttributeError, TypeError):
        pass


def _handle_command(state, cmd):
    """Parse and execute one UART command.

    Returns tuple: (action, payload)
      - ("SNAP", "") triggers a snapshot emit.
      - ("EMIT", "<line>") prints one status/ack line.
      - ("IGNORE", "") means no action.
    """
    if not cmd:
        return "IGNORE", ""

    if cmd == "!SNAP":
        return "SNAP", ""

    parts = cmd.split()
    if not parts:
        return "IGNORE", ""

    verb = parts[0].upper()

    if verb == "!HELP":
        return "EMIT", ">>CMD_OK HELP !SNAP !PAGE <0-5> !NEXT !SUBPAGE <0|1>"

    if verb == "!NEXT":
        page = _next_enabled_page(getattr(state, "page", 0), getattr(state, "enabled_pages", []))
        state.page = page
        _set_true_flag(state, "page_dirty")
        _set_true_flag(state, "status_dirty")
        if page == 4:
            try:
                state.metrics_subpage = 0
                _set_true_flag(state, "metrics_dirty")
            except (AttributeError, TypeError):
                pass
        return "EMIT", ">>CMD_OK NEXT %d" % page

    if verb == "!PAGE":
        if len(parts) != 2:
            return "EMIT", ">>CMD_ERR PAGE usage !PAGE <0-5>"
        try:
            page = int(parts[1])
        except (TypeError, ValueError):
            return "EMIT", ">>CMD_ERR PAGE invalid"

        pages = _normalize_enabled_pages(getattr(state, "enabled_pages", []))
        if page not in pages:
            return "EMIT", ">>CMD_ERR PAGE disabled"

        state.page = page
        _set_true_flag(state, "page_dirty")
        _set_true_flag(state, "status_dirty")
        if page == 4:
            try:
                state.metrics_subpage = 0
                _set_true_flag(state, "metrics_dirty")
            except (AttributeError, TypeError):
                pass
        return "EMIT", ">>CMD_OK PAGE %d" % page

    if verb == "!SUBPAGE":
        if len(parts) != 2:
            return "EMIT", ">>CMD_ERR SUBPAGE usage !SUBPAGE <0|1>"
        try:
            subpage = int(parts[1])
        except (TypeError, ValueError):
            return "EMIT", ">>CMD_ERR SUBPAGE invalid"
        if subpage not in (0, 1):
            return "EMIT", ">>CMD_ERR SUBPAGE range"

        state.metrics_subpage = subpage
        _set_true_flag(state, "metrics_dirty")
        _set_true_flag(state, "status_dirty")
        return "EMIT", ">>CMD_OK SUBPAGE %d" % subpage

    return "EMIT", ">>CMD_ERR unknown command"


async def uart_capture_task(state):
    """Async task: poll stdin for capture/menu-control UART commands."""
    if uselect is None:
        print("[SNAP] uselect unavailable - capture disabled")
        return

    try:
        poll = uselect.poll()
        poll.register(sys.stdin, uselect.POLLIN)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        print("[SNAP] poll setup failed: %s" % exc)
        return

    buf = []

    while True:
        try:
            events = poll.poll(0)
            while events:
                ch = sys.stdin.read(1)
                if not ch:
                    break

                if ch in ("\r", "\n"):
                    cmd = "".join(buf).strip()
                    buf.clear()
                    action, payload = _handle_command(state, cmd)
                    if action == "SNAP":
                        print("[SNAP] Snapshot triggered")
                        _emit_snapshot(state)
                    elif action == "EMIT":
                        print(payload)
                else:
                    buf.append(ch)
                    if len(buf) > 64:
                        buf.pop(0)

                events = poll.poll(0)
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            print("[SNAP] read error: %s" % exc)
            buf = []

        await asyncio.sleep_ms(50)
