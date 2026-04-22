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
from services.metrics_service import MetricsService
from services.solar_service import SolarService
from services.time_service import TimeService
from services.weather_service import WeatherService
from services.wifi_service import WifiService
from ui.display_manager import DisplayManager


async def button_task(state):
    # Use a pin IRQ flag so presses are captured even during blocking HTTP calls.
    # The IRQ fires immediately; the async loop processes it on the next 20ms tick.
    _flag = bytearray(1)   # IRQ-safe flag: set by ISR, cleared by async loop
    button = Pin(board.BTN_PIN, Pin.IN, Pin.PULL_UP)

    def _irq(_pin):
        _flag[0] = 1

    button.irq(trigger=Pin.IRQ_FALLING, handler=_irq)
    last_press_ms = 0

    while True:
        if _flag[0]:
            _flag[0] = 0
            now = ticks_ms()
            if ticks_diff(now, last_press_ms) > board.BTN_DEBOUNCE_MS:
                last_press_ms = now
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
        await asyncio.sleep_ms(20)


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
                await asyncio.sleep_ms(0)  # yield between blocking HTTP calls
                forecast = weather_svc.fetch_forecast()
                gc.collect()  # free parsed JSON payloads

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
            except (OSError, ValueError, RuntimeError) as exc:
                print("[PC] fetch error: %s" % exc)

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
            display.render(state, now_local, stale_ms, ticks_ms())

        await asyncio.sleep_ms(poll_ms)


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

    # Maximise contiguous free heap before the WiFi driver allocates its
    # ~16 KB RX-buffer pool.  The AppState + config dicts fragment the heap
    # enough to trigger "WiFi Out of Memory" if this is omitted.
    gc.collect()

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
        asyncio.create_task(esp_status_task(state, wifi_svc)),
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
