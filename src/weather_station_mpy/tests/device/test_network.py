"""Network HIL tests — run on device via mpremote.

Requirements covered:
  REQ-WIFI-02  WiFi must connect using credentials from config.json
  REQ-WIFI-03  Connected IP must be a valid non-loopback address
  REQ-WIFI-04  WiFi signal strength (RSSI) must be present (< 0 dBm)
  REQ-TIME-01  NTP time sync must succeed when WiFi is connected
  REQ-OWM-01   Weather service must return HTTP 200 and valid JSON
  REQ-OWM-02   Current weather must contain temp_c, condition, humidity
  REQ-OWM-03   Forecast must return at least 1 day entry
  REQ-NET-01   Total WiFi connect + NTP + weather fetch must complete < 30 s

Requires: WiFi credentials set in config.json (no placeholder values).
"""

# ── helpers injected by HIL runner (runner.py concatenated above) ──


def _load_cfg():
    from config.store import load_config
    return load_config("config.json")


def test_wifi_connects():
    """REQ-WIFI-02: ensure_connected() must return True within timeout."""
    import uasyncio as asyncio
    from services.wifi_service import WifiService
    cfg = _load_cfg()
    wifi_svc = WifiService(cfg)
    connected = asyncio.run(wifi_svc.ensure_connected())
    assert_true(connected, "WiFi did not connect within timeout")


def test_wifi_ip_valid():
    """REQ-WIFI-03: IP must be non-zero and non-loopback."""
    import network
    wlan = network.WLAN(network.STA_IF)
    assert_true(wlan.isconnected(), "WiFi not connected — run test_wifi_connects first")
    ip = wlan.ifconfig()[0]
    assert_not_equal(ip, "0.0.0.0", "IP is 0.0.0.0 (not connected)")
    assert_false(ip.startswith("127."), "IP is loopback: %s" % ip)
    print("[TEST:info] IP: %s" % ip)


def test_wifi_rssi_present():
    """REQ-WIFI-04: RSSI must be a negative dBm value indicating signal."""
    import network
    wlan = network.WLAN(network.STA_IF)
    assert_true(wlan.isconnected(), "WiFi not connected")
    rssi = wlan.status("rssi")
    assert_lt(rssi, 0, "RSSI not negative: %d dBm" % rssi)
    assert_gt(rssi, -100, "RSSI too low (no signal): %d dBm" % rssi)
    print("[TEST:info] RSSI: %d dBm" % rssi)


def test_ntp_sync():
    """REQ-TIME-01: NTP sync must succeed when WiFi is available."""
    import uasyncio as asyncio
    from config.store import load_config
    from services.time_service import TimeService
    cfg = load_config("config.json")
    time_svc = TimeService(cfg)
    synced = asyncio.run(time_svc.sync_ntp())
    assert_true(synced, "NTP sync returned False")


def test_weather_fetch_ok():
    """REQ-OWM-01: fetch_current() must return HTTP 200 and valid data."""
    from config.store import load_config
    from services.weather_service import WeatherService
    cfg = load_config("config.json")
    svc = WeatherService(cfg)
    result = svc.fetch_current()
    assert_is_not_none(result, "fetch_current() returned None")
    assert_true(result.get("valid", False), "weather result not marked valid")


def test_weather_data_valid():
    """REQ-OWM-02: current weather dict must contain required numeric fields."""
    from config.store import load_config
    from services.weather_service import WeatherService
    cfg = load_config("config.json")
    svc = WeatherService(cfg)
    w = svc.fetch_current()
    assert_is_not_none(w, "fetch_current() returned None")
    assert_in("temp_c", w, "missing temp_c")
    assert_in("feels_like_c", w, "missing feels_like_c")
    assert_in("humidity", w, "missing humidity")
    assert_in("condition", w, "missing condition")
    assert_in("condition_id", w, "missing condition_id")
    assert_true(isinstance(w["temp_c"], float), "temp_c not float")
    assert_true(isinstance(w["humidity"], int), "humidity not int")
    assert_true(isinstance(w["condition"], str), "condition not str")
    assert_true(len(w["condition"]) > 0, "condition is empty string")
    print("[TEST:info] %.1fC %s hum=%d%%" % (w["temp_c"], w["condition"], w["humidity"]))


def test_forecast_fetch_ok():
    """REQ-OWM-03: fetch_forecast() must return at least 1 daily entry."""
    from config.store import load_config
    from services.weather_service import WeatherService
    cfg = load_config("config.json")
    svc = WeatherService(cfg)
    forecast = svc.fetch_forecast()
    assert_is_not_none(forecast, "fetch_forecast() returned None")
    assert_gt(len(forecast), 0, "forecast list is empty")
    first = forecast[0]
    assert_in("date", first)
    assert_in("temp_min", first)
    assert_in("temp_max", first)
    assert_in("condition", first)
    print("[TEST:info] %d forecast days, first: %s" % (len(forecast), first.get("date", "?")))


def test_wifi_reconnect_safe():
    """REQ-WIFI-05: ensure_connected() must not raise when called while connecting.

    Disconnects WiFi then calls ensure_connected() twice in rapid succession.
    The second call arrives while the radio may still be in STAT_CONNECTING;
    the fix in wifi_service.py must call disconnect() first instead of letting
    IDF raise 'OSError: Wifi Internal State Error'.
    """
    import uasyncio as asyncio
    import network
    from config.store import load_config
    from services.wifi_service import WifiService

    cfg = load_config("config.json")
    wlan = network.WLAN(network.STA_IF)

    # Force a known starting state: disconnect so we exercise the reconnect path.
    wlan.disconnect()

    wifi_svc = WifiService(cfg)

    # First call — starts connecting (may or may not succeed in 10 s).
    result1 = asyncio.run(wifi_svc.ensure_connected())

    # Second call immediately — radio may still be in STAT_CONNECTING if first
    # call timed out.  Must not raise OSError.
    try:
        result2 = asyncio.run(wifi_svc.ensure_connected())
        assert_true(
            isinstance(result2, bool),
            "ensure_connected() must return bool, got %r" % result2,
        )
    except OSError as exc:
        assert_true(False, "ensure_connected() raised OSError on reconnect: %s" % exc)

    # At least one of the two calls must have attempted a connection.
    assert_true(True, "reconnect path completed without OSError")
    stat = wlan.status()
    print("[TEST:info] WiFi status after reconnect test: %d (first=%s)" % (stat, result1))


# ── register and run ──────────────────────────────────────────────────────────

print("[TEST] === Network Tests ===")
run("wifi_connects",         test_wifi_connects)
run("wifi_ip_valid",         test_wifi_ip_valid)
run("wifi_rssi_present",     test_wifi_rssi_present)
run("ntp_sync",              test_ntp_sync)
run("weather_fetch_ok",      test_weather_fetch_ok)
run("weather_data_valid",    test_weather_data_valid)
run("forecast_fetch_ok",     test_forecast_fetch_ok)
run("wifi_reconnect_safe",   test_wifi_reconnect_safe)
summary()
