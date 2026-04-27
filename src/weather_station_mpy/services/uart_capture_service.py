"""UART display-state snapshot service.

Listens on stdin for the command ``!SNAP\\n`` and dumps a JSON
summary of the current AppState between ``>>SNAP_START`` and
``>>SNAP_END`` markers.  The output is designed to be consumed by
the PC-side ``tools/capture_display.py`` script.

Usage on device (background async task — added in main.py):
    from services.uart_capture_service import uart_capture_task
    asyncio.create_task(uart_capture_task(state))

Trigger from PC (raw serial, e.g. via miniterm or capture_display.py):
    !SNAP<CR><LF>
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


def _safe(value, default=None):
    """Return value if JSON-serialisable, else default."""
    try:
        ujson.dumps(value)
        return value
    except Exception:
        return default


def _build_snapshot(state):
    """Build a serialisable dict from the current AppState."""
    return {
        "page": _safe(state.page, 0),
        "enabled_pages": _safe(state.enabled_pages, []),
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
    except Exception as exc:
        print(">>SNAP_ERR %s" % exc)
    gc.collect()


async def uart_capture_task(state):
    """Async task: poll stdin every 200 ms for the '!SNAP' trigger."""
    if uselect is None:
        print("[SNAP] uselect unavailable — capture disabled")
        return

    try:
        poll = uselect.poll()
        poll.register(sys.stdin, uselect.POLLIN)
    except Exception as exc:
        print("[SNAP] poll setup failed: %s" % exc)
        return

    buf = []

    while True:
        try:
            events = poll.poll(0)
            if events:
                ch = sys.stdin.read(1)
                if ch in ("\r", "\n"):
                    cmd = "".join(buf).strip()
                    buf.clear()
                    if cmd == "!SNAP":
                        print("[SNAP] Snapshot triggered")
                        _emit_snapshot(state)
                else:
                    buf.append(ch)
                    if len(buf) > 20:
                        buf.pop(0)
        except Exception as exc:
            print("[SNAP] read error: %s" % exc)
            buf = []

        await asyncio.sleep_ms(200)
