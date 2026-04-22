"""Tests for WifiService — init path and connectivity helpers.

The network module is unavailable on CPython, so these tests exercise the
no-network fallback path and the ensure_connected timeout logic.
"""

import unittest
import sys
import os
from unittest.mock import MagicMock, patch, AsyncMock

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
        """REQ-WIFI-05: when status != STAT_IDLE, disconnect() before connect().

        Reproduces OSError: Wifi Internal State Error on IDF v5.5.1 — after a
        connect() timeout the radio stays in STAT_CONNECTING (status=1); the
        next retry must call disconnect() first to reset the state machine.
        """
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=1, std_asyncio=std_asyncio
            )
            await svc.ensure_connected()
            mock_wlan.disconnect.assert_called_once()
            mock_wlan.connect.assert_called_once()
        finally:
            wf_module.asyncio = original_asyncio

    async def test_disconnect_not_called_when_status_is_idle(self):
        """REQ-WIFI-05: when status == STAT_IDLE (0), do not call disconnect()."""
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=0, std_asyncio=std_asyncio
            )
            await svc.ensure_connected()
            mock_wlan.disconnect.assert_not_called()
            mock_wlan.connect.assert_called_once()
        finally:
            wf_module.asyncio = original_asyncio

    async def test_connect_oserror_returns_false(self):
        """REQ-WIFI-05: if connect() raises OSError, ensure_connected must return False."""
        import services.wifi_service as wf_module
        import asyncio as std_asyncio

        original_asyncio = wf_module.asyncio
        try:
            svc, mock_wlan = await self._make_svc_with_status(
                wf_module, status_val=0, std_asyncio=std_asyncio
            )
            mock_wlan.connect.side_effect = OSError("Wifi Internal State Error")
            result = await svc.ensure_connected()
            self.assertFalse(result)
        finally:
            wf_module.asyncio = original_asyncio


if __name__ == "__main__":
    unittest.main()
