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
    # After SW_CPU_RESET (crash recovery) the WiFi driver auto-reconnects
    # using stored NVS credentials.  The RF scan uses GDMA; if spi_bus_initialize()
    # races the scan, spicommon_periph_claim() fails → host_id stays NULL →
    # Guru Meditation (LoadProhibited at EXCVADDR:0x74) on the first SPI write.
    # Calling disconnect() cancels the scan and returns the radio to STAT_IDLE
    # so GDMA is free when the display initialises SPI shortly after.
    try:
        _wlan.disconnect()
    except OSError:
        pass  # already idle — disconnect() is a no-op, OSError is expected
    # Wait for the radio to fully idle and for any in-flight GDMA transfers
    # to complete before SPI bus init runs.
    _time.sleep_ms(200)
    del _time
    del _wlan
    gc.collect()
    print("[BOOT] WiFi STA interface ready")
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
