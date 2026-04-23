"""MicroPython boot script.

This keeps startup side effects minimal and leaves app logic to main.py.
"""

import gc

print("[BOOT] weather_station_mpy boot.py")
gc.collect()

# Pre-initialise the WiFi STA interface HERE, before main.py loads any
# modules.  network.WLAN(STA_IF) calls esp_wifi_init() which needs ~16 KB
# of contiguous heap for 10 RX buffers.  Doing this at boot-time (clean
# heap) avoids the "WiFi Out of Memory" OOM that occurs after imports
# fragment the heap.
try:
    import network as _net
    _wlan = _net.WLAN(_net.STA_IF)
    # Only activate if not already active — never cycle active(False)+active(True).
    # Cycling free/reallocates the 16 KB WiFi RX buffers and fragments heap,
    # which can prevent the SPI DMA allocator from finding a contiguous region.
    if not _wlan.active():
        _wlan.active(True)
    import time as _time
    # esp_wifi_start() in IDF v5.x is asynchronous: allow 300 ms for the
    # WiFi FreeRTOS task to start on Core 0 before we call disconnect().
    _time.sleep_ms(300)
    # On soft reset, clear any lingering STA state from the previous runtime.
    # This keeps SPI-safe behavior while avoiding sticky auth/connect states.
    from machine import reset_cause as _reset_cause, PWRON_RESET as _PWRON_RESET
    _is_poweron = (_reset_cause() == _PWRON_RESET)
    del _reset_cause, _PWRON_RESET
    if not _is_poweron:
        try:
            _wlan.disconnect()
        except OSError:
            pass  # already idle — disconnect() is a no-op, OSError is expected
        _time.sleep_ms(150)
        try:
            _wlan.active(False)
            _time.sleep_ms(250)
            _wlan.active(True)
            _time.sleep_ms(350)
        except Exception as _wifi_reset_err:
            print("[BOOT] WiFi STA cycle skipped:", _wifi_reset_err)
    del _is_poweron
    del _time
    _mac = _wlan.config("mac")
    _mac_str = "%02x:%02x:%02x:%02x:%02x:%02x" % tuple(_mac)
    del _mac
    print("[BOOT] WiFi STA interface ready — MAC: %s" % _mac_str)
    del _mac_str

    del _wlan
    gc.collect()
except Exception as _e:
    print("[BOOT] WiFi pre-init failed:", _e)
finally:
    gc.collect()

# ── SPI2 stale-state cleanup ──────────────────────────────────────────────────
# On a MicroPython soft reset (Ctrl-D), the Python finalizer calls
# spi_bus_free() but WiFi GDMA on Core 0 can leave spi_host_t.hal.hw = NULL.
# The next spi_bus_initialize() then fails silently; every transaction returns
# "invalid dev handle", and the NULL-pointer deref causes a Guru Meditation
# LoadProhibited crash.  Acquiring SPI2 here and calling deinit() forces the
# IDF driver back to a clean "unregistered" state before display_manager runs.
# This is a no-op after a hardware reset because the bus isn't initialized yet.
try:
    from machine import SPI as _SPI, Pin as _Pin
    import board as _board
    _spi_cleanup = _SPI(
        _board.SPI_BUS,
        1_000_000,
        sck=_Pin(_board.LCD_SCLK),
        mosi=_Pin(_board.LCD_MOSI),
    )
    _spi_cleanup.deinit()
    del _spi_cleanup
    del _SPI, _Pin, _board
    gc.collect()
    print("[BOOT] SPI2 stale state cleared")
except Exception as _e:
    # On a fresh hardware reset the bus isn't initialized yet — ignore.
    print("[BOOT] SPI2 cleanup skipped:", _e)
finally:
    gc.collect()
