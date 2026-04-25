"""MicroPython boot script.

This keeps startup side effects minimal and leaves app logic to main.py.
"""

import gc

print("[BOOT] weather_station_mpy boot.py")
gc.collect()

# ── WiFi DMA pool pre-reservation ────────────────────────────────────────────
# active(True) may trigger an IDF NVS auto-connect that fails and caches a
# stale disconnect reason (204/15) in volatile driver memory.  disconnect()
# alone does NOT clear this reason.  active(False) is the only operation that
# fully resets the IDF WiFi FSM and clears all cached state.
# Perform active(False)→active(True) here while the heap is still clean and
# unfragmented (before main.py imports), so the DMA buffer re-allocation
# succeeds and the driver starts in a guaranteed-clean state.
try:
    import network as _net
    import time as _time
    _wlan = _net.WLAN(_net.STA_IF)
    if _wlan.active():
        try:
            _wlan.active(False)
        except OSError:
            pass
        _time.sleep_ms(300)
    _wlan.active(True)
    try:
        _wlan.config(reconnects=0)
    except OSError:
        pass
    try:
        _wlan.disconnect()
    except OSError:
        pass
    _time.sleep_ms(300)
    print("[BOOT] WiFi DMA pool reserved (clean state)")
    del _wlan, _net, _time
    gc.collect()
except Exception as _e:
    print("[BOOT] WiFi pre-alloc failed:", _e)
finally:
    gc.collect()

# ── SPI2 stale-state cleanup ──────────────────────────────────────────────────
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
    print("[BOOT] SPI2 cleanup skipped:", _e)
finally:
    gc.collect()
