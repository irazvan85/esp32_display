"""Wi-Fi connectivity helper for ESP32 MicroPython."""

import gc

try:
    network = __import__("network")
except ImportError:
    network = None

try:
    asyncio = __import__("uasyncio")
except ImportError:
    import asyncio

from compat import ticks_diff, ticks_ms


class WifiService:
    def __init__(self, cfg):
        self._cfg = cfg
        if network is None:
            self._wlan = None
            return

        # Attach to the STA interface that boot.py pre-initialised.
        # network.WLAN(STA_IF) at this point returns the existing singleton
        # without re-running esp_wifi_init(), so no OOM risk from heap
        # fragmentation.  A gc.collect() is still useful to consolidate heap
        # before any subsequent socket allocations.
        gc.collect()
        self._wlan = network.WLAN(network.STA_IF)
        if not self._wlan.active():
            # boot.py pre-init may have failed; try to activate now.
            self._wlan.active(True)

    def is_connected(self):
        if self._wlan is None:
            return False
        return self._wlan.isconnected()

    def ip(self):
        if self._wlan is None:
            return "0.0.0.0"
        if not self._wlan.isconnected():
            return "0.0.0.0"
        return self._wlan.ifconfig()[0]

    async def ensure_connected(self):
        if self._wlan is None:
            return False

        if self._wlan.isconnected():
            return True

        ssid = self._cfg["wifi"]["ssid"]
        password = self._cfg["wifi"]["password"]
        timeout_ms = int(self._cfg["wifi"].get("connect_timeout_ms", 10_000))

        # If the radio is still in a non-idle state (e.g. STAT_CONNECTING from a
        # previous timeout), reset it first.  On IDF v5.5.1 calling connect()
        # while status != STAT_IDLE (0) raises "OSError: Wifi Internal State
        # Error" — the underlying WiFi task keeps trying even after the
        # MicroPython-level timeout elapses.
        try:
            if self._wlan.status() != 0:  # 0 == network.STAT_IDLE
                self._wlan.disconnect()
                await asyncio.sleep_ms(300)
        except OSError:
            pass

        print("[WiFi] Connecting to %s" % ssid)
        try:
            self._wlan.connect(ssid, password)
        except OSError as exc:
            print("[WiFi] connect() error: %s" % exc)
            return False

        started = ticks_ms()
        while (not self._wlan.isconnected()) and (
            ticks_diff(ticks_ms(), started) < timeout_ms
        ):
            await asyncio.sleep_ms(200)

        if self._wlan.isconnected():
            print("[WiFi] Connected, IP: %s" % self.ip())
            return True

        print("[WiFi] Connect timeout - offline mode")
        return False
