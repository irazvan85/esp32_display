"""Tests for WifiService simplified connect behavior."""

import asyncio as std_asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestWifiServiceNoNetwork(unittest.TestCase):
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
    def _make_svc_with_mock_network(self, wlan_active=True):
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
        _, _, mock_network = self._make_svc_with_mock_network(wlan_active=True)
        self.assertEqual(mock_network.WLAN.call_count, 1)

    def test_active_not_called_when_already_active(self):
        _, mock_wlan, _ = self._make_svc_with_mock_network(wlan_active=True)
        active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
        self.assertEqual(len(active_true_calls), 0)

    def test_active_true_called_when_interface_inactive(self):
        _, mock_wlan, _ = self._make_svc_with_mock_network(wlan_active=False)
        active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
        self.assertEqual(len(active_true_calls), 1)


class TestWifiServiceEnsureConnected(unittest.IsolatedAsyncioTestCase):
    def test_parse_bssid_valid_and_invalid(self):
        from services.wifi_service import WifiService

        svc = WifiService.__new__(WifiService)
        self.assertEqual(
            svc._parse_bssid("aa:bb:cc:dd:ee:ff"),
            bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF]),
        )
        self.assertIsNone(svc._parse_bssid("aa:bb:cc:dd:ee"))
        self.assertIsNone(svc._parse_bssid("aa:bb:cc:dd:ee:gg"))

    async def test_ensure_connected_returns_false_when_no_wlan(self):
        from services.wifi_service import WifiService

        svc = WifiService({"wifi": {"ssid": "x", "password": "y"}})
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

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(ms):
                await std_asyncio.sleep(ms / 1000)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertFalse(result)
            self.assertEqual(mock_wlan.connect.call_count, 1)
            # _prepare_sta_for_connect now unconditionally calls disconnect()
            self.assertTrue(mock_wlan.disconnect.called)
            # Verify disconnect is called AFTER active(False) and active(True) are called
            active_false_idx = None
            active_true_idx = None
            disconnect_idx = None
            for i, c in enumerate(mock_wlan.method_calls):
                if c == call.active(False):
                    active_false_idx = i
                elif c == call.active(True):
                    active_true_idx = i
                elif c == call.disconnect():
                    disconnect_idx = i
            self.assertIsNotNone(active_false_idx, "active(False) should be called")
            self.assertIsNotNone(active_true_idx, "active(True) should be called")
            self.assertIsNotNone(disconnect_idx, "disconnect() should be called")
            self.assertLess(active_false_idx, active_true_idx)
            self.assertLess(active_true_idx, disconnect_idx)
            # active(True) and active(False) each called once (from _prepare_sta_for_connect)
            active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
            active_false_calls = [c for c in mock_wlan.active.call_args_list if c.args == (False,)]
            self.assertEqual(len(active_true_calls), 1)
            self.assertEqual(len(active_false_calls), 1)
        finally:
            wf_module.asyncio = original_asyncio

    async def test_pre_status_connecting_disconnects_before_connect(self):
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False
        mock_wlan.status.return_value = 1001
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(ms):
                await std_asyncio.sleep(ms / 1000)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertFalse(result)
            self.assertTrue(mock_wlan.disconnect.called)
            self.assertEqual(mock_wlan.connect.call_count, 1)
            self.assertLess(
                mock_wlan.method_calls.index(call.disconnect()),
                mock_wlan.method_calls.index(call.connect("x", "y")),
            )
        finally:
            wf_module.asyncio = original_asyncio

    async def test_pre_status_terminal_error_disconnects_before_connect(self):
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False
        mock_wlan.status.return_value = 201
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(ms):
                await std_asyncio.sleep(ms / 1000)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertFalse(result)
            self.assertTrue(mock_wlan.disconnect.called)
            self.assertEqual(mock_wlan.connect.call_count, 1)
            self.assertLess(
                mock_wlan.method_calls.index(call.disconnect()),
                mock_wlan.method_calls.index(call.connect("x", "y")),
            )
        finally:
            wf_module.asyncio = original_asyncio

    async def test_no_early_break_on_terminal_status_in_loop(self):
        """New behaviour: terminal status codes do NOT break the loop early."""
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False
        mock_wlan.status.return_value = 202
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(ms):
                await std_asyncio.sleep(ms / 1000)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertFalse(result)
            self.assertEqual(mock_wlan.connect.call_count, 1)
            # _prepare_sta_for_connect calls active(False) and active(True)
            active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
            active_false_calls = [c for c in mock_wlan.active.call_args_list if c.args == (False,)]
            self.assertEqual(len(active_true_calls), 1)
            self.assertEqual(len(active_false_calls), 1)
        finally:
            wf_module.asyncio = original_asyncio

    async def test_disconnect_called_before_connect_in_normal_path(self):
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False
        mock_wlan.status.return_value = 1000
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(ms):
                await std_asyncio.sleep(ms / 1000)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertFalse(result)
            self.assertTrue(mock_wlan.disconnect.called)
            self.assertEqual(mock_wlan.connect.call_count, 1)
            self.assertLess(
                mock_wlan.method_calls.index(call.disconnect()),
                mock_wlan.method_calls.index(call.connect("x", "y")),
            )
        finally:
            wf_module.asyncio = original_asyncio

    async def test_no_sta_cycle_on_status_15(self):
        """Status polling loop does NOT break early on status=15 (or any status code)."""
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False
        mock_wlan.status.return_value = 15
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(ms):
                await std_asyncio.sleep(ms / 1000)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertFalse(result)
            self.assertEqual(mock_wlan.connect.call_count, 1)
            # _prepare_sta_for_connect calls active(False) and active(True)
            active_true_calls = [c for c in mock_wlan.active.call_args_list if c.args == (True,)]
            active_false_calls = [c for c in mock_wlan.active.call_args_list if c.args == (False,)]
            self.assertEqual(len(active_true_calls), 1)
            self.assertEqual(len(active_false_calls), 1)
        finally:
            wf_module.asyncio = original_asyncio

    async def test_ensure_connected_uses_configured_bssid(self):
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {
            "wifi": {
                "ssid": "home",
                "password": "secret",
                "bssid": "aa:bb:cc:dd:ee:ff",
                "connect_timeout_ms": 1000,
            }
        }
        mock_wlan = MagicMock()
        conn_checks = {"n": 0}

        def _isconnected():
            conn_checks["n"] += 1
            return conn_checks["n"] >= 3

        mock_wlan.isconnected.side_effect = _isconnected
        mock_wlan.ifconfig.return_value = ("192.168.1.50", "", "", "")
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(_ms):
                await std_asyncio.sleep(0)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertTrue(result)
            self.assertEqual(mock_wlan.connect.call_count, 1)
            self.assertEqual(
                mock_wlan.connect.call_args,
                call("home", "secret", bssid=bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])),
            )
        finally:
            wf_module.asyncio = original_asyncio

    async def test_prepare_sta_for_connect_sequence(self):
        """Validates _prepare_sta_for_connect() calls active(False), active(True), config, and disconnect() in order."""
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 50}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = False
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(_ms):
                await std_asyncio.sleep(0)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertFalse(result)

            # Verify the sequence of calls in _prepare_sta_for_connect
            active_false_idx = None
            active_true_idx = None
            config_idx = None
            disconnect_idx = None

            for i, c in enumerate(mock_wlan.method_calls):
                if c == call.active(False):
                    active_false_idx = i
                elif c == call.active(True):
                    active_true_idx = i
                elif c == call.config(reconnects=0):
                    config_idx = i
                elif c == call.disconnect():
                    disconnect_idx = i

            self.assertIsNotNone(active_false_idx, "active(False) should be called")
            self.assertIsNotNone(active_true_idx, "active(True) should be called")
            self.assertIsNotNone(config_idx, "config(reconnects=0) should be called")
            self.assertIsNotNone(disconnect_idx, "disconnect() should be called")

            # Verify sequence: active(False) -> active(True) -> config -> disconnect
            self.assertLess(active_false_idx, active_true_idx)
            self.assertLess(active_true_idx, config_idx)
            self.assertLess(config_idx, disconnect_idx)

            # Verify connect is called after prep is done
            connect_idx = None
            for i, c in enumerate(mock_wlan.method_calls):
                if c == call.connect("x", "y"):
                    connect_idx = i
                    break

            self.assertIsNotNone(connect_idx, "connect() should be called")
            self.assertLess(disconnect_idx, connect_idx, "disconnect (from prep) should be called before connect()")

        finally:
            wf_module.asyncio = original_asyncio

    async def test_ensure_connected_early_return_when_already_connected(self):
        """Validates that ensure_connected() returns early and does NOT call _prepare_sta_for_connect when already connected."""
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {"wifi": {"ssid": "x", "password": "y", "connect_timeout_ms": 1000}}
        mock_wlan = MagicMock()
        mock_wlan.isconnected.return_value = True
        mock_wlan.ifconfig.return_value = ("192.168.1.100", "", "", "")
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(_ms):
                await std_asyncio.sleep(0)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertTrue(result)
            # Verify that active(), config(), disconnect(), and connect() are NOT called
            # (i.e., early return means no prep and no connect attempt)
            active_calls = [c for c in mock_wlan.active.call_args_list]
            config_calls = [c for c in mock_wlan.method_calls if c == call.config(reconnects=0)]
            disconnect_calls = [c for c in mock_wlan.method_calls if c == call.disconnect()]
            connect_calls = [c for c in mock_wlan.method_calls if "connect" in str(c) and c != call.isconnected()]

            self.assertEqual(len(active_calls), 0, "active() should NOT be called when already connected")
            self.assertEqual(len(config_calls), 0, "config() should NOT be called when already connected")
            self.assertEqual(len(disconnect_calls), 0, "disconnect() should NOT be called when already connected")
            self.assertEqual(len(connect_calls), 0, "connect() should NOT be called when already connected")

        finally:
            wf_module.asyncio = original_asyncio

    async def test_ensure_connected_configured_bssid_typeerror_falls_back(self):
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {
            "wifi": {
                "ssid": "home",
                "password": "secret",
                "bssid": "aa:bb:cc:dd:ee:ff",
                "connect_timeout_ms": 1000,
            }
        }
        mock_wlan = MagicMock()
        conn_checks = {"n": 0}

        def _isconnected():
            conn_checks["n"] += 1
            return conn_checks["n"] >= 3

        mock_wlan.isconnected.side_effect = _isconnected
        mock_wlan.ifconfig.return_value = ("192.168.1.50", "", "", "")

        def _connect(*_args, **kwargs):
            if "bssid" in kwargs:
                raise TypeError("unexpected keyword argument bssid")
            return None

        mock_wlan.connect.side_effect = _connect
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(_ms):
                await std_asyncio.sleep(0)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertTrue(result)
            self.assertEqual(mock_wlan.connect.call_count, 2)
            self.assertEqual(
                mock_wlan.connect.call_args_list,
                [
                    call("home", "secret", bssid=bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])),
                    call("home", "secret"),
                ],
            )
        finally:
            wf_module.asyncio = original_asyncio

    async def test_status15_retries_with_scanned_bssid(self):
        from services.wifi_service import WifiService
        import services.wifi_service as wf_module

        svc = WifiService.__new__(WifiService)
        svc._cfg = {
            "wifi": {
                "ssid": "home",
                "password": "secret",
                "bssid": "",
                "connect_timeout_ms": 80,
            }
        }

        mock_wlan = MagicMock()

        second_attempt_status = {"idx": 0}

        def _status():
            # First connect attempt: force ASSOC_FAIL path.
            if mock_wlan.connect.call_count < 2:
                return 15

            # Second connect attempt: move to connecting then got-ip states.
            seq = (1001, 1010)
            i = second_attempt_status["idx"]
            if i < len(seq):
                second_attempt_status["idx"] += 1
                return seq[i]
            return 1010

        second_attempt_conn_checks = {"n": 0}

        def _isconnected():
            # Never connected on first attempt.
            if mock_wlan.connect.call_count < 2:
                return False
            # Connected on second attempt after a short status poll window.
            second_attempt_conn_checks["n"] += 1
            return second_attempt_conn_checks["n"] >= 3

        mock_wlan.status.side_effect = _status
        mock_wlan.isconnected.side_effect = _isconnected
        mock_wlan.ifconfig.return_value = ("192.168.1.50", "", "", "")
        mock_wlan.scan.return_value = [
            (b"home", bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF]), 6, -52, 4, 0),
            (b"home", bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06]), 1, -80, 4, 0),
        ]
        svc._wlan = mock_wlan

        original_asyncio = wf_module.asyncio
        try:
            async def _sleep_ms(_ms):
                await std_asyncio.sleep(0)

            mock_aio = MagicMock()
            mock_aio.sleep_ms = _sleep_ms
            wf_module.asyncio = mock_aio

            result = await svc.ensure_connected()
            self.assertTrue(result)
            self.assertEqual(mock_wlan.connect.call_count, 2)
            self.assertEqual(
                mock_wlan.connect.call_args_list[1],
                call("home", "secret", bssid=bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])),
            )
        finally:
            wf_module.asyncio = original_asyncio


if __name__ == "__main__":
    unittest.main()
