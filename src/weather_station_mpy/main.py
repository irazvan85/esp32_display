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
from compat import mem_free, ticks_diff, ticks_ms
from config.store import ConfigNotReadyError, load_config
from services.metrics_service import MetricsService
from services.solar_service import SolarService
from services.time_service import TimeService
from services.weather_service import WeatherService
from services.wifi_service import WifiService
from ui.display_manager import DisplayManager


async def button_task(state):
    button = Pin(board.BTN_PIN, Pin.IN, Pin.PULL_UP)
    prev = button.value()
    last_press = 0

    while True:
        now = ticks_ms()
        cur = button.value()
        if prev == 1 and cur == 0:
            if ticks_diff(now, last_press) > board.BTN_DEBOUNCE_MS:
                last_press = now
                if state.page == board.PAGE_PC_MONITOR:
                    gpu_available = state.metrics.get("gpu_pct") is not None
                    if gpu_available and state.metrics_subpage == 0:
                        state.metrics_subpage = 1
                        state.metrics_dirty = True
                        print("[BTN] PC view B (GPU)")
                    else:
                        state.metrics_subpage = 0
                        state.page = (state.page + 1) % board.TOTAL_PAGES
                        state.page_dirty = True
                        print("[BTN] Page -> %d" % state.page)
                else:
                    state.page = (state.page + 1) % board.TOTAL_PAGES
                    state.page_dirty = True
                    print("[BTN] Page -> %d" % state.page)
        prev = cur
        await asyncio.sleep_ms(30)


async def wifi_task(state, wifi_svc, cfg, time_svc):
    wifi_check_ms = int(cfg["wifi"].get("check_interval_ms", 30_000))

    while True:
        online = await wifi_svc.ensure_connected()
        changed = online != state.wifi_online
        state.wifi_online = online

        if changed:
            state.status_dirty = True
            state.page_dirty = True
            if online:
                state.time_synced = await time_svc.sync_ntp()

        await asyncio.sleep_ms(wifi_check_ms)


async def weather_task(state, weather_svc, cfg):
    if not bool(cfg["weather"].get("enabled", True)):
        print("[OWM] Disabled in config")
        return

    refresh_ms = int(cfg["weather"].get("refresh_ms", 600_000))

    while True:
        if state.wifi_online:
            try:
                weather = weather_svc.fetch_current()
                forecast = weather_svc.fetch_forecast()

                state.weather = weather
                state.forecast = forecast
                state.weather_dirty = True
                state.forecast_dirty = True
                state.status_dirty = True
                state.last_weather_fetch_ms = weather["fetched_ms"]
                state.last_forecast_fetch_ms = ticks_ms()

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

        await asyncio.sleep_ms(refresh_ms)


async def metrics_task(state, metrics_svc, cfg):
    if not metrics_svc.enabled():
        print("[PC] Disabled in config")
        return

    refresh_ms = int(cfg["metrics"].get("refresh_ms", 10_000))

    while True:
        if state.wifi_online:
            try:
                metrics = metrics_svc.fetch()
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
            except (OSError, ValueError, RuntimeError) as exc:
                print("[PC] fetch error: %s" % exc)

        await asyncio.sleep_ms(refresh_ms)


async def render_task(state, display, time_svc, cfg):
    stale_ms = int(cfg["weather"].get("stale_ms", 1_800_000))
    clock_ms = int(cfg["time"].get("clock_refresh_ms", 1_000))

    while True:
        now_local = None
        if state.time_synced:
            try:
                now_local = time_svc.now_localtime()
            except OSError:
                now_local = None

        if display.ready:
            display.render(state, now_local, stale_ms, ticks_ms())

        await asyncio.sleep_ms(clock_ms)


async def memory_log_task():
    while True:
        gc.collect()
        print("[MEM] Free heap: %d bytes" % mem_free())
        await asyncio.sleep(60)


async def app_main():
    print("\n================================================")
    print("  Retro Weather Clock MicroPython")
    print("================================================\n")

    display = DisplayManager()
    display.init()
    display.draw_boot("Booting...")

    try:
        cfg = load_config("config.json")
    except ConfigNotReadyError as exc:
        print("[CFG] %s" % exc)
        display.draw_config_error(str(exc))
        while True:
            await asyncio.sleep(5)

    state = AppState()
    state.metrics_stale_ms = int(cfg.get("metrics", {}).get("stale_ms", 120_000))

    wifi_svc = WifiService(cfg)
    time_svc = TimeService(cfg)
    weather_svc = WeatherService(cfg)
    solar_svc = SolarService(cfg)
    metrics_svc = MetricsService(cfg)

    # Initial connectivity attempt.
    state.wifi_online = await wifi_svc.ensure_connected()
    if state.wifi_online:
        state.time_synced = await time_svc.sync_ntp()

    tasks = [
        asyncio.create_task(button_task(state)),
        asyncio.create_task(wifi_task(state, wifi_svc, cfg, time_svc)),
        asyncio.create_task(weather_task(state, weather_svc, cfg)),
        asyncio.create_task(solar_task(state, solar_svc, cfg)),
        asyncio.create_task(metrics_task(state, metrics_svc, cfg)),
        asyncio.create_task(render_task(state, display, time_svc, cfg)),
        asyncio.create_task(memory_log_task()),
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
