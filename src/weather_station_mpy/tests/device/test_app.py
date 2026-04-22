"""App-level HIL tests — run on device via mpremote.

Requirements covered:
  REQ-CFG-01   config.json must load without ConfigNotReadyError
  REQ-CFG-02   WiFi credentials and OWM API key must not be placeholders
  REQ-STATE-01 AppState must initialise with all dirty flags set to True
  REQ-STATE-02 mark_all_dirty() must set every dirty flag
  REQ-ESP-01   esp_status metrics must return cpu_mhz > 0 and ram_free_kb > 0
  REQ-SVC-01   All service classes must instantiate without error
  REQ-PAGE-01  Page constants must satisfy: PAGE_ESP_STATUS < TOTAL_PAGES == 6
  REQ-TASK-01  Main app tasks must be creatable and not raise on first tick

No outgoing network calls.
"""

# ── helpers injected by HIL runner (runner.py concatenated above) ──


def test_config_loads():
    """REQ-CFG-01: load_config() must succeed without ConfigNotReadyError."""
    from config.store import load_config, ConfigNotReadyError
    try:
        cfg = load_config("config.json")
        assert_is_not_none(cfg, "load_config returned None")
        assert_true(isinstance(cfg, dict), "config is not a dict")
    except ConfigNotReadyError as e:
        raise AssertionError("ConfigNotReadyError: %s" % e)


def test_config_no_placeholders():
    """REQ-CFG-02: real credentials must be set (no your_* values)."""
    from config.store import load_config
    cfg = load_config("config.json")
    ssid = cfg.get("wifi", {}).get("ssid", "")
    assert_false(ssid.lower().startswith("your_"),
                 "wifi.ssid is still a placeholder: %r" % ssid)
    api_key = cfg.get("weather", {}).get("api_key", "")
    assert_false(api_key.lower().startswith("your_"),
                 "weather.api_key is still a placeholder")


def test_appstate_init():
    """REQ-STATE-01: AppState must start with all dirty flags True."""
    from app_state import AppState
    s = AppState()
    for flag in ("page_dirty", "weather_dirty", "forecast_dirty", "solar_dirty",
                 "metrics_dirty", "clock_dirty", "date_dirty", "esp_status_dirty"):
        assert_true(getattr(s, flag), "flag not set on init: %s" % flag)
    assert_false(s.wifi_online, "wifi_online should be False at init")
    assert_false(s.time_synced, "time_synced should be False at init")
    assert_equal(s.page, 0, "initial page must be 0")
    assert_equal(s.metrics_subpage, 0, "initial metrics_subpage must be 0")
    assert_true(isinstance(s.esp_status, dict), "esp_status must be a dict")
    assert_equal(len(s.esp_status), 0, "esp_status must be empty at init")


def test_mark_all_dirty():
    """REQ-STATE-02: mark_all_dirty() must flip every flag to True."""
    from app_state import AppState
    s = AppState()
    # Clear all flags
    for flag in ("page_dirty", "status_dirty", "weather_dirty", "forecast_dirty",
                 "solar_dirty", "metrics_dirty", "clock_dirty", "date_dirty",
                 "esp_status_dirty"):
        setattr(s, flag, False)
    s.mark_all_dirty()
    for flag in ("page_dirty", "status_dirty", "weather_dirty", "forecast_dirty",
                 "solar_dirty", "metrics_dirty", "clock_dirty", "date_dirty",
                 "esp_status_dirty"):
        assert_true(getattr(s, flag), "mark_all_dirty() missed flag: %s" % flag)


def test_esp_status_collect():
    """REQ-ESP-01: ESP system metrics must return sensible values."""
    import gc
    import machine
    import esp
    import uos

    gc.collect()

    cpu_mhz = machine.freq() // 1_000_000
    assert_gt(cpu_mhz, 0, "cpu_mhz is 0")
    assert_ge(cpu_mhz, 80, "cpu_mhz too low: %d" % cpu_mhz)

    flash_kb = esp.flash_size() // 1024
    assert_gt(flash_kb, 0, "flash_kb is 0")

    sv = uos.statvfs("/")
    fs_total = (sv[0] * sv[2]) // 1024
    assert_gt(fs_total, 0, "fs_total_kb is 0")

    ram_free = gc.mem_free() // 1024
    assert_gt(ram_free, 20, "ram_free too low: %d KB" % ram_free)

    ram_used = gc.mem_alloc() // 1024
    assert_ge(ram_used, 0, "ram_used is negative")

    print("[TEST:info] CPU=%dMHz flash=%dKB fs=%dKB ram_free=%dKB"
          % (cpu_mhz, flash_kb, fs_total, ram_free))


def test_services_instantiate():
    """REQ-SVC-01: all service classes must instantiate cleanly."""
    from config.store import load_config
    from services.wifi_service import WifiService
    from services.time_service import TimeService
    from services.weather_service import WeatherService
    from services.solar_service import SolarService
    from services.metrics_service import MetricsService

    cfg = load_config("config.json")

    wifi_svc  = WifiService(cfg)
    time_svc  = TimeService(cfg)
    wx_svc    = WeatherService(cfg)
    sol_svc   = SolarService(cfg)
    met_svc   = MetricsService(cfg)

    assert_is_not_none(wifi_svc,  "WifiService init returned None")
    assert_is_not_none(time_svc,  "TimeService init returned None")
    assert_is_not_none(wx_svc,    "WeatherService init returned None")
    assert_is_not_none(sol_svc,   "SolarService init returned None")
    assert_is_not_none(met_svc,   "MetricsService init returned None")

    # Verify WifiService has the STA wlan handle
    # (None is acceptable on CPython, but on device it must be set)
    assert_is_not_none(wifi_svc._wlan, "WifiService._wlan is None on device")


def test_display_manager_init():
    """REQ-DISP-01: DisplayManager must initialise the ST7789 without error."""
    from ui.display_manager import DisplayManager
    dm = DisplayManager()
    dm.init()
    assert_true(dm.ready, "DisplayManager.ready is False after init()")


def test_page_count():
    """REQ-PAGE-01: TOTAL_PAGES == 6 and all PAGE_* constants fit within it."""
    import board
    assert_equal(board.TOTAL_PAGES, 6)
    assert_lt(board.PAGE_PC_MONITOR, board.TOTAL_PAGES)
    assert_lt(board.PAGE_ESP_STATUS, board.TOTAL_PAGES)


def test_compat_mem_helpers():
    """REQ-MEM-02: compat mem helpers must return positive ints on device."""
    from compat import mem_free, mem_alloc
    assert_gt(mem_free(),  0, "mem_free() not positive on device")
    assert_ge(mem_alloc(), 0, "mem_alloc() negative on device")


# ── register and run ──────────────────────────────────────────────────────────

print("[TEST] === App Tests ===")
run("config_loads",             test_config_loads)
run("config_no_placeholders",   test_config_no_placeholders)
run("appstate_init",            test_appstate_init)
run("mark_all_dirty",           test_mark_all_dirty)
run("esp_status_collect",       test_esp_status_collect)
run("services_instantiate",     test_services_instantiate)
run("display_manager_init",     test_display_manager_init)
run("page_count",               test_page_count)
run("compat_mem_helpers",       test_compat_mem_helpers)
summary()
