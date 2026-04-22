"""Wi-Fi connectivity helper for ESP32 MicroPython."""

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

        self._wlan = network.WLAN(network.STA_IF)
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

        print("[WiFi] Connecting to %s" % ssid)
        self._wlan.connect(ssid, password)

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
