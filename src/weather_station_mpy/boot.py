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
    # esp_wifi_start() in IDF v5.x is asynchronous: it returns immediately
    # while the WiFi task initialises in the background.  Without a small
    # pause the SPI bus init that follows shortly after in main.py can race
    # against the WiFi driver's DMA / peripheral setup and fail with
    # "host_id not initialized".  500 ms ensures the WiFi task reaches
    # STAT_IDLE on both warm (soft-reset) and cold (power-on) boots.
    import time as _time
    _time.sleep_ms(500)
    del _time
    del _wlan
    gc.collect()
    print("[BOOT] WiFi STA interface ready")
except Exception as _e:
    print("[BOOT] WiFi pre-init failed:", _e)
finally:
    gc.collect()
