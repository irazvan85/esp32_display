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

# MicroPython ESP32 network status constants.
# STAT_IDLE = 1000, STAT_CONNECTING = 1001, STAT_GOT_IP = 1010
# Error states (terminal, no point waiting longer): 200-204
_STAT_IDLE = getattr(network, "STAT_IDLE", 1000) if network else 1000
_STAT_CONNECTING = getattr(network, "STAT_CONNECTING", 1001) if network else 1001
_STAT_GOT_IP = getattr(network, "STAT_GOT_IP", 1010) if network else 1010
# Any status in this set means the connection has terminally failed
_STAT_TERMINAL_ERRORS = frozenset({200, 201, 202, 203, 204})


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
        timeout_ms = int(self._cfg["wifi"].get("connect_timeout_ms", 15_000))

        current_status = self._wlan.status()
        print("[WiFi] pre-connect status=%d" % current_status)

        # Reset the radio to STAT_IDLE before issuing connect().
        # Skip only when already idle (STAT_IDLE = 1000).
        #   • STAT_CONNECTING (1001): must cancel the active attempt; calling
        #     connect() while connecting raises "Wifi Internal State Error".
        #   • Terminal errors 200-204 (auth fail, no AP, etc.): the AP already
        #     terminated the connection but the IDF state machine has not
        #     returned to IDLE yet; connect() on this state also raises
        #     "Wifi Internal State Error".
        # Previously the check used `!= 0` (always True since STAT_IDLE = 1000,
        # not 0) which was semantically correct but used the wrong constant.
        if current_status != _STAT_IDLE:
            try:
                self._wlan.disconnect()
                await asyncio.sleep_ms(500)
                print("[WiFi] post-disconnect status=%d" % self._wlan.status())
            except OSError:
                pass

        print("[WiFi] Connecting to %s" % ssid)
        try:
            self._wlan.connect(ssid, password)
        except OSError as exc:
            print("[WiFi] connect() error: %s" % exc)
            return False

        # Grace period: connect() is asynchronous on Core 0. The IDF state
        # machine takes ~200-500 ms to transition from the previous attempt's
        # status to STAT_CONNECTING (1001). Checking status() before this
        # transition would see a stale code and trigger a false early exit.
        # The timeout window starts AFTER this settle delay.
        await asyncio.sleep_ms(500)
        started = ticks_ms()

        last_status = -1
        while (not self._wlan.isconnected()) and (
            ticks_diff(ticks_ms(), started) < timeout_ms
        ):
            st = self._wlan.status()
            if st != last_status:
                print("[WiFi] status=%d" % st)  # log every state transition
                last_status = st
            # Terminal error: AP has rejected us or can't be found — the WiFi
            # task has already stopped, no point waiting for the full timeout.
            if st in _STAT_TERMINAL_ERRORS:
                print("[WiFi] Connection failed (status=%d)" % st)
                break
            await asyncio.sleep_ms(200)

        if self._wlan.isconnected():
            print("[WiFi] Connected, IP: %s" % self.ip())
            return True

        final_status = self._wlan.status()

        # Force a one-shot STA restart so the next retry starts from a clean state.
        # Two cases require this:
        #   • CONNECTING timeout: IDF state machine stuck in CONNECTING (1001).
        #   • Terminal error (200-204, especially 202 = auth-fail): disconnect()
        #     returns status to IDLE (1000) but IDF retains a persistent
        #     wifi_sta_disconn_reason code.  The next connect() reads that code
        #     and fails immediately with 202 even when credentials are correct.
        #     active(False/True) is the only reliable way to clear this reason.
        if final_status == _STAT_CONNECTING or final_status in _STAT_TERMINAL_ERRORS:
            print("[WiFi] clearing stale state (status=%d) - cycling STA" % final_status)
            try:
                self._wlan.active(False)
                await asyncio.sleep_ms(300)
                self._wlan.active(True)
                await asyncio.sleep_ms(500)
                print("[WiFi] STA reset complete (status=%d)" % self._wlan.status())
            except OSError as exc:
                print("[WiFi] STA reset failed: %s" % exc)

        # No explicit cleanup: the next ensure_connected() call will check
        # status and call disconnect() via the non-idle guard above.
        print("[WiFi] offline (status=%d)" % self._wlan.status())
        return False
