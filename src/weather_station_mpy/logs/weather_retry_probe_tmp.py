import gc

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import sys

for path in ("/", ""):
    if path not in sys.path:
        sys.path.append(path)

from config.store import load_config
from services.wifi_service import WifiService
from services.weather_service import WeatherService


async def _ensure_wifi(cfg):
    wifi = WifiService(cfg)
    ok = await wifi.ensure_connected()
    print("[PROBE] wifi_connected=%s" % ok)
    return ok


async def _delay_5s():
    await asyncio.sleep_ms(5000)


def _probe_weather(cfg):
    weather = WeatherService(cfg)

    for idx in range(1, 7):
        try:
            current = weather.fetch_current()
            temp_c = current.get("temp_c") if isinstance(current, dict) else None
            condition = current.get("condition") if isinstance(current, dict) else None
            print(
                "[PROBE] current attempt=%d ok temp_c=%s condition=%s"
                % (idx, temp_c, condition)
            )
        except Exception as exc:
            print("[PROBE] current attempt=%d err %r" % (idx, exc))

        if idx < 6:
            asyncio.run(_delay_5s())

    try:
        forecast = weather.fetch_forecast()
        length = len(forecast) if forecast is not None else 0
        print("[PROBE] forecast ok len=%d" % length)
    except Exception as exc:
        print("[PROBE] forecast err %r" % exc)


if __name__ == "__main__":
    try:
        cfg = load_config("config.json")
        print("[PROBE] config loaded")
    except Exception as exc:
        print("[PROBE] config load err %r" % exc)
        raise

    asyncio.run(_ensure_wifi(cfg))
    gc.collect()
    _probe_weather(cfg)
