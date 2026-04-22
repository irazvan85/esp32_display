"""Minimal SPI diagnostic: test if SPI write works after WiFi init."""
import gc

print("[DIAG] SPI diagnostic")
gc.collect()
print("[DIAG] Heap:", gc.mem_free())

import network
wlan = network.WLAN(network.STA_IF)
print("[DIAG] WiFi active:", wlan.active())
gc.collect()
print("[DIAG] Heap post-WiFi:", gc.mem_free())

from machine import SPI, Pin
print("[DIAG] SPI init...")
try:
    spi = SPI(2, baudrate=40_000_000, polarity=0, phase=0,
              sck=Pin(18), mosi=Pin(23))
    print("[DIAG] SPI created:", spi)
    dc = Pin(2, Pin.OUT)
    cs = Pin(15, Pin.OUT)
    cs(0); dc(0)
    spi.write(b'\x01')
    cs(1)
    print("[DIAG] SPI write OK")
    spi.deinit()
except Exception as e:
    print("[DIAG] SPI FAIL:", type(e).__name__, e)

print("[DIAG] Done")
