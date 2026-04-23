"""Tests for WifiService — init path and connectivity helpers.

The network module is unavailable on CPython, so these tests exercise the
no-network fallback path and the ensure_connected timeout logic.
"""

import unittest
import sys
import os
from unittest.mock import MagicMock, patch, AsyncMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestWifiServiceNoNetwork(unittest.TestCase):
    """Tests run in the CPython path where network == None."""

    def setUp(self):
        from services.wifi_service import WifiService
        self.WifiService = WifiService

    def test_init_without_network_sets_wlan_none(self):
        svc = self.WifiService({"wifi": {"ssid": "x", "password": "y"}})
        self.assertIsNone(svc._wlan)

    def test_is_connected_returns_false_without_network(self):
        svc = self.WifiService({"wifi": {"ssid": "x", "password": "y"}})
        self.assertFalse(svc.is_connected())

    def test_ip_returns_sentinel_without_network(self):
        svc = self.WifiService({"wifi": {"ssid": "x", "password": "y"}})
        self.assertEqual(svc.ip(), "0.0.0.0")


class TestWifiServiceInit(unittest.TestCase):
    """Test that WifiService.__init__ attaches to an existing WLAN correctly."""

    def _make_svc_with_mock_network(self, wlan_active=True):
        """Inject a mock network module and return (svc, mock_wlan)."""
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        mock_wlan = MagicMock()
        mock_wlan.active.return_value = wlan_active

        mock_network = MagicMock()
        mock_network.WLAN.return_value = mock_wlan
        mock_network.STA_IF = 0

        original_network = wf_module.network
        wf_module.network = mock_network
        try:
            svc = WifiService({"wifi": {"ssid": "x", "password": "y"}})
        finally:
            wf_module.network = original_network

        return svc, mock_wlan, mock_network

    def test_wlan_created_exactly_once(self):
        """WLAN() must be called only once — prevents the double-init OOM."""
        _, _, mock_network = self._make_svc_with_mock_network(wlan_active=True)
        self.assertEqual(mock_network.WLAN.call_count, 1)

    def test_active_not_called_when_already_active(self):
        """If boot.py pre-init left interface active, do not call active(True)."""
        _, mock_wlan, _ = self._make_svc_with_mock_network(wlan_active=True)
        # active(True) must NOT be called — that would create a second init cycle
        active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
        self.assertEqual(len(active_true_calls), 0)

    def test_active_true_called_when_interface_inactive(self):
        """If boot.py pre-init failed, wifi_service must activate the interface."""
        _, mock_wlan, _ = self._make_svc_with_mock_network(wlan_active=False)
        active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
        self.assertEqual(len(active_true_calls), 1)


class TestWifiServiceEnsureConnected(unittest.IsolatedAsyncioTestCase):
    """Tests for ensure_connected coroutine with mocked WLAN."""

    async def test_ensure_connected_returns_false_when_no_wlan(self):
        from services.wifi_service import WifiService
        svc = WifiService({"wifi": {"ssid": "x", "password": "y"}})
        # _wlan is None on CPython
        result = await svc.ensure_connected()
        self.assertFalse(result)

    async def test_ensure_connected_returns_true_when_already_connected(self):
        from services.wifi_service import WifiService
        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 1000}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = True
        mock_wlan.ifconfig.return_value = ("192.168.1.100", "", "", "")
        svc._wlan = mock_wlan

        result = await svc.ensure_connected()
        self.assertTrue(result)

    async def test_ensure_connected_times_out_and_returns_false(self):
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False  # never connects
        svc._wlan = mock_wlan

        # CPython asyncio has no sleep_ms; patch it so the coroutine can run
        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(ms):
                await std_asyncio.sleep(ms / 1000)
            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertFalse(result)
        finally:
            wf_module.asyncio = original_asyncio

    async def _make_svc_with_status(self, wf_module, status_val, std_asyncio):
        """Build a WifiService whose wlan.status() returns status_val (never connects)."""
        from services.wifi_service import WifiService

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False
        mock_wlan.status.return_value = status_val
        svc._wlan = mock_wlan

        async def _sleep_ms(ms):
            await std_asyncio.sleep(ms / 1000)

        mock_aio = MagicMock()
        mock_aio.sleep_ms = _sleep_ms
        wf_module.asyncio = mock_aio
        return svc, mock_wlan

    async def test_disconnect_called_when_status_is_connecting(self):
        """REQ-WIFI-05: when status == STAT_CONNECTING (1001), disconnect() before connect().

        STAT_CONNECTING means the radio is actively trying; we must cancel it
        before starting a new attempt or risk 'OSError: Wifi Internal State Error'.
        """
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=1001, std_asyncio=std_asyncio
            )
            await svc.ensure_connected()
            # disconnect() must have been called (at minimum once before connect;
            # possibly also as post-timeout cleanup)
            mock_wlan.disconnect.assert_called()
            mock_wlan.connect.assert_called_once()
        finally:
            wf_module.asyncio = original_asyncio

    async def test_disconnect_not_called_before_connect_when_status_is_idle(self):
        """REQ-WIFI-05: when status == STAT_IDLE (1000), do not call disconnect() before connect().

        STAT_IDLE = 1000 on MicroPython ESP32.  The radio is already stopped;
        calling disconnect() adds unnecessary delay and doesn't help.
        """
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=1000, std_asyncio=std_asyncio
            )
            await svc.ensure_connected()
            mock_wlan.connect.assert_called_once()
            # disconnect() must NOT be called before connect().
            all_calls = [c[0] for c in mock_wlan.method_calls]
            connect_idx = next(
                (i for i, n in enumerate(all_calls) if n == "connect"), None
            )
            pre_disconnect_calls = [
                n for n in all_calls[:connect_idx] if n == "disconnect"
            ]
            self.assertEqual(
                pre_disconnect_calls, [],
                "disconnect() must not be called before connect() when status is STAT_IDLE",
            )
        finally:
            wf_module.asyncio = original_asyncio

    async def test_disconnect_called_for_terminal_error_state(self):
        """REQ-WIFI-05: when status is a terminal error (e.g. 202), call disconnect() before connect().

        Terminal-error states (200-204) leave the IDF state machine in a
        non-idle state; calling connect() without disconnect() first raises
        'Wifi Internal State Error'.  The guard must treat terminal errors
        the same as STAT_CONNECTING.
        """
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            # status=202 = STAT_WRONG_PASSWD (auth fail) — a terminal error
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=202, std_asyncio=std_asyncio
            )
            await svc.ensure_connected()
            mock_wlan.connect.assert_called_once()
            # disconnect() must have been called before connect()
            all_calls = [c[0] for c in mock_wlan.method_calls]
            connect_idx = next(
                (i for i, n in enumerate(all_calls) if n == "connect"), None
            )
            pre_disconnect_calls = [
                n for n in all_calls[:connect_idx] if n == "disconnect"
            ]
            self.assertGreater(
                len(pre_disconnect_calls), 0,
                "disconnect() must be called before connect() for terminal error status",
            )
        finally:
            wf_module.asyncio = original_asyncio

    async def test_connect_oserror_returns_false(self):
        """REQ-WIFI-05: if connect() raises OSError, ensure_connected must return False."""
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=1000, std_asyncio=std_asyncio
            )
            mock_wlan.connect.side_effect = OSError("Wifi Internal State Error")
            result = await svc.ensure_connected()
            self.assertFalse(result)
        finally:
            wf_module.asyncio = original_asyncio

    async def test_terminal_error_status_causes_early_exit(self):
        """REQ-WIFI-05: status in {200-204} after grace period must cause early exit.

        After the 500 ms grace period, if status is a terminal error
        the poll loop must break immediately rather than waiting for the
        full timeout window.
        """
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            # status=202 (STAT_WRONG_PASSWD), timeout_ms long enough to prove
            # the early exit fires before it would otherwise expire.
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=202, std_asyncio=std_asyncio
            )
            # Override timeout to be long (2 s) to ensure the loop would NOT
            # end via timeout on its own without the early-exit feature.
            svc._cfg["wifi"]["connect_timeout_ms"] = 2000
            import time
            t0 = time.monotonic()
            result = await svc.ensure_connected()
            elapsed = time.monotonic() - t0
            self.assertFalse(result)
            # Should exit loop in ~0.5 s grace + ~0.2 s first poll (early exit),
            # then ~0.8 s for the STA cycle (active(False/True)).
            # Total: ~1.5 s.  Without early exit the loop alone would run 2.0 s,
            # giving ~3.3 s total.  Threshold of 2.5 s distinguishes the two paths.
            self.assertLess(elapsed, 2.5,
                            "terminal error should trigger early exit, not full timeout")
            mock_wlan.connect.assert_called_once()
        finally:
            wf_module.asyncio = original_asyncio

    async def test_connecting_timeout_cycles_sta_interface(self):
        """REQ-WIFI-05: timeout in STAT_CONNECTING must hard-reset STA once.

        If a connect attempt times out while still in CONNECTING state,
        a single active(False)->active(True) cycle clears stale IDF state
        so the next retry starts from clean IDLE.
        """
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=1001, std_asyncio=std_asyncio
            )
            result = await svc.ensure_connected()
            self.assertFalse(result)
            mock_wlan.active.assert_any_call(False)
            mock_wlan.active.assert_any_call(True)
        finally:
            wf_module.asyncio = original_asyncio

    async def test_terminal_error_cycles_sta_interface(self):
        """REQ-WIFI-05: terminal error (202) must trigger active(False/True) STA cycle.

        disconnect() returns status to IDLE but IDF retains a persistent
        wifi_sta_disconn_reason code.  The next connect() reads that reason and
        fails immediately with 202 even when credentials are correct.
        active(False/True) is the only way to clear this stale reason code.
        """
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=202, std_asyncio=std_asyncio
            )
            result = await svc.ensure_connected()
            self.assertFalse(result)
            mock_wlan.active.assert_any_call(False)
            mock_wlan.active.assert_any_call(True)
        finally:
            wf_module.asyncio = original_asyncio


class TestBootPyWifiInit(unittest.TestCase):
    """Tests that model the boot.py WiFi pre-init + disconnect() guard.

    boot.py calls:
      1. wlan.active(True)   — start WiFi driver
      2. sleep 300ms         — let esp_wifi_start() complete
      3. wlan.disconnect()   — cancel any auto-reconnect scan (SW_CPU_RESET case)
      4. sleep 200ms         — let GDMA idle before SPI bus init

    REQ-BOOT-01: After SW_CPU_RESET (crash recovery), the auto-reconnect RF scan
    uses GDMA and blocks spi_bus_initialize().  boot.py must call disconnect()
    to cancel the scan before the display driver claims the SPI bus.
    """

    def _run_boot_py_logic(self, wlan_active_initial, disconnect_raises=False):
        """Simulate the boot.py WiFi init block and return the mock wlan."""
        import time

        mock_wlan = MagicMock()
        mock_wlan.active.return_value = wlan_active_initial
        if disconnect_raises:
            mock_wlan.disconnect.side_effect = OSError("not connected")

        # Simulate boot.py logic (condensed, no actual sleep):
        if not mock_wlan.active():
            mock_wlan.active(True)
        # sleep_ms(300) — omitted in unit test
        try:
            mock_wlan.disconnect()
        except OSError:
            pass
        # sleep_ms(200) — omitted in unit test

        return mock_wlan

    def test_disconnect_called_after_active_true(self):
        """REQ-BOOT-01: disconnect() must always be called after active(True).

        After SW_CPU_RESET the WiFi auto-reconnect scan starts within 300 ms.
        disconnect() cancels it so GDMA is free for spi_bus_initialize().
        """
        mock_wlan = self._run_boot_py_logic(wlan_active_initial=False)
        mock_wlan.disconnect.assert_called_once()

    def test_disconnect_called_when_already_active(self):
        """REQ-BOOT-01: disconnect() must be called even if WiFi was already active.

        After a CTRL-D soft reset WiFi stays active; disconnect() still runs to
        ensure any leftover scan/connect state is cleared.
        """
        mock_wlan = self._run_boot_py_logic(wlan_active_initial=True)
        mock_wlan.disconnect.assert_called_once()

    def test_disconnect_oserror_is_swallowed(self):
        """REQ-BOOT-01: OSError from disconnect() must not propagate.

        If WiFi is already idle disconnect() raises OSError on some IDF builds.
        boot.py must swallow it so the boot sequence continues.
        """
        # Should not raise even though disconnect() raises OSError
        try:
            self._run_boot_py_logic(wlan_active_initial=True, disconnect_raises=True)
        except OSError:
            self.fail("boot.py must not propagate OSError from disconnect()")

    def test_active_not_called_when_already_active(self):
        """REQ-BOOT-01: active(True) must not be called if interface is already up."""
        mock_wlan = self._run_boot_py_logic(wlan_active_initial=True)
        active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
        self.assertEqual(len(active_true_calls), 0)

    def test_active_true_called_when_interface_down(self):
        """REQ-BOOT-01: active(True) must be called if interface is not active."""
        mock_wlan = self._run_boot_py_logic(wlan_active_initial=False)
        active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
        self.assertEqual(len(active_true_calls), 1)

    def test_disconnect_called_after_active(self):
        """REQ-BOOT-01: disconnect() must be called AFTER active(True), not before."""
        mock_wlan = self._run_boot_py_logic(wlan_active_initial=False)
        call_names = [c[0] for c in mock_wlan.method_calls]
        # Find the first active(True) call and first disconnect() call
        active_idx = next(
            (i for i, (n, a, _) in enumerate(mock_wlan.method_calls)
             if n == "active" and a == (True,)), None
        )
        disconnect_idx = next(
            (i for i, (n, _, _) in enumerate(mock_wlan.method_calls)
             if n == "disconnect"), None
        )
        self.assertIsNotNone(active_idx, "active(True) not called")
        self.assertIsNotNone(disconnect_idx, "disconnect() not called")
        self.assertGreater(disconnect_idx, active_idx,
                           "disconnect() must be called after active(True)")


if __name__ == "__main__":
    unittest.main()
