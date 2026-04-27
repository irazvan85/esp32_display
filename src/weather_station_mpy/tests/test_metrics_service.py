"""Tests for MetricsService — value clamping and response parsing.

HTTP calls are mocked; no network access required.
"""

import unittest
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.metrics_service import MetricsService


class TestMetricsServiceHelpers(unittest.TestCase):
    def test_pct_clamps_low(self):
        self.assertAlmostEqual(MetricsService._pct(-5.0), 0.0)

    def test_pct_clamps_high(self):
        self.assertAlmostEqual(MetricsService._pct(105.0), 100.0)

    def test_pct_passthrough(self):
        self.assertAlmostEqual(MetricsService._pct(55.0), 55.0)

    def test_opt_pct_none(self):
        self.assertIsNone(MetricsService._opt_pct(None))

    def test_opt_pct_clamps(self):
        self.assertAlmostEqual(MetricsService._opt_pct(-1.0), 0.0)
        self.assertAlmostEqual(MetricsService._opt_pct(200.0), 100.0)

    def test_opt_pct_passthrough(self):
        self.assertAlmostEqual(MetricsService._opt_pct(72.5), 72.5)

    def test_opt_float_none(self):
        self.assertIsNone(MetricsService._opt_float(None))

    def test_opt_float_converts(self):
        self.assertAlmostEqual(MetricsService._opt_float("37.5"), 37.5)
        self.assertAlmostEqual(MetricsService._opt_float(42), 42.0)


class TestMetricsServiceEnabled(unittest.TestCase):
    def test_enabled_true(self):
        svc = MetricsService({"metrics": {"enabled": True}})
        self.assertTrue(svc.enabled())

    def test_enabled_false(self):
        svc = MetricsService({"metrics": {"enabled": False}})
        self.assertFalse(svc.enabled())

    def test_enabled_missing_key(self):
        svc = MetricsService({})
        self.assertFalse(svc.enabled())


class TestMetricsServiceFetch(unittest.TestCase):
    _CFG = {
        "metrics": {
            "pc_url": "http://192.168.1.1:8765/api/system/metrics",
            "timeout_ms": 3000,
        }
    }

    def test_fetch_parses_full_payload(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "valid": True,
            "cpu_pct": 42.5,
            "ram_pct": 61.0,
            "disk_pct": 88.0,
            "temp_c": 55.5,
            "gpu_pct": 30.0,
            "gpu_temp_c": 65.0,
            "uptime_s": 3600,
            "ts": 1000000,
        }

        import services.metrics_service as ms_module
        original_requests = ms_module.requests
        try:
            ms_module.requests = MagicMock()
            ms_module.requests.get.return_value = mock_response

            svc = MetricsService(self._CFG)
            result = svc.fetch()

            self.assertTrue(result["valid"])
            self.assertAlmostEqual(result["cpu_pct"], 42.5)
            self.assertAlmostEqual(result["ram_pct"], 61.0)
            self.assertAlmostEqual(result["disk_pct"], 88.0)
            self.assertAlmostEqual(result["temp_c"], 55.5)
            self.assertAlmostEqual(result["gpu_pct"], 30.0)
            self.assertAlmostEqual(result["gpu_temp_c"], 65.0)
            self.assertEqual(result["uptime_s"], 3600)
        finally:
            ms_module.requests = original_requests

    def test_fetch_handles_missing_gpu(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "valid": True,
            "cpu_pct": 10.0,
            "ram_pct": 20.0,
            "disk_pct": 30.0,
            "temp_c": None,
            "uptime_s": 0,
            "ts": 0,
        }

        import services.metrics_service as ms_module
        original_requests = ms_module.requests
        try:
            ms_module.requests = MagicMock()
            ms_module.requests.get.return_value = mock_response

            svc = MetricsService(self._CFG)
            result = svc.fetch()

            self.assertIsNone(result["temp_c"])
            self.assertIsNone(result["gpu_pct"])
            self.assertIsNone(result["gpu_temp_c"])
        finally:
            ms_module.requests = original_requests

    def test_fetch_raises_on_http_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 503

        import services.metrics_service as ms_module
        original_requests = ms_module.requests
        try:
            ms_module.requests = MagicMock()
            ms_module.requests.get.return_value = mock_response

            svc = MetricsService(self._CFG)
            with self.assertRaises(RuntimeError):
                svc.fetch()
        finally:
            ms_module.requests = original_requests

    def test_pct_clamping_applied_to_fetch(self):
        """Values outside 0–100 must be clamped, not passed through raw."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "valid": True,
            "cpu_pct": 150.0,  # > 100
            "ram_pct": -5.0,   # < 0
            "disk_pct": 50.0,
            "temp_c": None,
            "uptime_s": 0,
            "ts": 0,
        }

        import services.metrics_service as ms_module
        original_requests = ms_module.requests
        try:
            ms_module.requests = MagicMock()
            ms_module.requests.get.return_value = mock_response

            svc = MetricsService(self._CFG)
            result = svc.fetch()

            self.assertAlmostEqual(result["cpu_pct"], 100.0)
            self.assertAlmostEqual(result["ram_pct"], 0.0)
        finally:
            ms_module.requests = original_requests

    def test_fetch_prefers_socket_for_ipv4_literal_host(self):
        import services.metrics_service as ms_module

        cfg = {
            "metrics": {
                "pc_url": "http://192.168.1.77:8765/api/system/metrics",
                "timeout_ms": 3000,
            }
        }
        svc = MetricsService(cfg)

        svc._fetch_json_socket = MagicMock(
            return_value={
                "valid": True,
                "cpu_pct": 12.5,
                "ram_pct": 33.0,
                "disk_pct": 44.0,
                "temp_c": 50.0,
                "uptime_s": 10,
                "ts": 20,
            }
        )
        svc._get = MagicMock(side_effect=AssertionError("_get should not be used"))

        original_requests = ms_module.requests
        try:
            ms_module.requests = MagicMock()

            result = svc.fetch()

            svc._fetch_json_socket.assert_called_once_with(
                "192.168.1.77", 8765, "/api/system/metrics", 3
            )
            svc._get.assert_not_called()
            self.assertAlmostEqual(result["cpu_pct"], 12.5)
        finally:
            ms_module.requests = original_requests

    def test_fetch_uses_urequests_path_for_hostname(self):
        import services.metrics_service as ms_module

        cfg = {
            "metrics": {
                "pc_url": "http://pc-host.local:8765/api/system/metrics",
                "timeout_ms": 3000,
            }
        }
        svc = MetricsService(cfg)

        svc._fetch_json_socket = MagicMock(
            side_effect=AssertionError("socket path must not be used for hostname")
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "valid": True,
            "cpu_pct": 5.0,
            "ram_pct": 6.0,
            "disk_pct": 7.0,
            "temp_c": None,
            "uptime_s": 8,
            "ts": 9,
        }
        svc._get = MagicMock(return_value=mock_response)

        original_requests = ms_module.requests
        try:
            ms_module.requests = MagicMock()

            result = svc.fetch()

            svc._fetch_json_socket.assert_not_called()
            svc._get.assert_called_once_with(
                "http://pc-host.local:8765/api/system/metrics", 3
            )
            self.assertAlmostEqual(result["cpu_pct"], 5.0)
        finally:
            ms_module.requests = original_requests


class TestMetricsServiceSocketPath(unittest.TestCase):
    def test_is_ipv4_literal_true_and_false_cases(self):
        self.assertTrue(MetricsService._is_ipv4_literal("192.168.1.10"))
        self.assertTrue(MetricsService._is_ipv4_literal("0.0.0.0"))
        self.assertTrue(MetricsService._is_ipv4_literal("255.255.255.255"))

        self.assertFalse(MetricsService._is_ipv4_literal(""))
        self.assertFalse(MetricsService._is_ipv4_literal("192.168.1"))
        self.assertFalse(MetricsService._is_ipv4_literal("192.168.1.256"))
        self.assertFalse(MetricsService._is_ipv4_literal("192.168.one.10"))
        self.assertFalse(MetricsService._is_ipv4_literal("pc-host.local"))

    def test_fetch_json_socket_raises_runtimeerror_on_non_200(self):
        import services.metrics_service as ms_module

        class _FakeSocket:
            def settimeout(self, _timeout):
                return None

            def connect(self, _addr):
                return None

            def send(self, _data):
                return None

            def recv(self, _size):
                if not hasattr(self, "_returned"):
                    self._returned = True
                    return b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 2\r\n\r\n{}"
                return b""

            def close(self):
                return None

        class _FakeSocketModule:
            AF_INET = 2
            SOCK_STREAM = 1

            @staticmethod
            def socket(_af, _sock_type):
                return _FakeSocket()

        original_socket = ms_module.socket
        try:
            ms_module.socket = _FakeSocketModule()
            with self.assertRaises(RuntimeError) as ctx:
                MetricsService._fetch_json_socket("192.168.1.10", 8765, "/api", 3)
            self.assertIn("HTTP 503", str(ctx.exception))
        finally:
            ms_module.socket = original_socket

    def test_fetch_json_socket_raises_runtimeerror_on_malformed_http(self):
        import services.metrics_service as ms_module

        class _FakeSocket:
            def settimeout(self, _timeout):
                return None

            def connect(self, _addr):
                return None

            def send(self, _data):
                return None

            def recv(self, _size):
                if not hasattr(self, "_returned"):
                    self._returned = True
                    return b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n{\"valid\":true}"
                return b""

            def close(self):
                return None

        class _FakeSocketModule:
            AF_INET = 2
            SOCK_STREAM = 1

            @staticmethod
            def socket(_af, _sock_type):
                return _FakeSocket()

        original_socket = ms_module.socket
        try:
            ms_module.socket = _FakeSocketModule()
            with self.assertRaises(RuntimeError) as ctx:
                MetricsService._fetch_json_socket("192.168.1.10", 8765, "/api", 3)
            self.assertIn("malformed response", str(ctx.exception))
        finally:
            ms_module.socket = original_socket


class TestMetricsServiceConnectivity(unittest.TestCase):
    def test_parse_http_url_with_explicit_port_and_path(self):
        host, port, path = MetricsService._parse_http_url(
            "http://192.168.1.25:8765/api/system/metrics"
        )

        self.assertEqual(host, "192.168.1.25")
        self.assertEqual(port, 8765)
        self.assertEqual(path, "/api/system/metrics")

    def test_parse_http_url_without_scheme_uses_default_port(self):
        host, port, path = MetricsService._parse_http_url("pc-host.local/api/system/metrics")

        self.assertEqual(host, "pc-host.local")
        self.assertEqual(port, 80)
        self.assertEqual(path, "/api/system/metrics")

    def test_connectivity_diag_resolve_and_connect_success(self):
        import services.metrics_service as ms_module

        cfg = {"metrics": {"pc_url": "http://pc-host.local:8765/api/system/metrics"}}
        svc = MetricsService(cfg)

        class _FakeSocketInstance:
            def __init__(self):
                self.timeout = None
                self.connected_addr = None
                self.closed = False

            def settimeout(self, timeout):
                self.timeout = timeout

            def connect(self, addr):
                self.connected_addr = addr

            def close(self):
                self.closed = True

        class _FakeSocketModule:
            SOCK_STREAM = 1
            AF_INET = 2

            def __init__(self):
                self.created = []

            def getaddrinfo(self, host, port, *_args):
                return [(None, None, None, None, ("192.168.1.99", port))]

            def socket(self, _af, _sock_type):
                inst = _FakeSocketInstance()
                self.created.append(inst)
                return inst

        class _FakeWlan:
            def ifconfig(self):
                return ("192.168.1.20", "255.255.255.0", "192.168.1.1", "8.8.8.8")

        class _FakeNetwork:
            STA_IF = 0

            @staticmethod
            def WLAN(_if_id):
                return _FakeWlan()

        def _fake_import(name, *_args, **_kwargs):
            if name == "network":
                return _FakeNetwork
            raise ImportError(name)

        original_socket = ms_module.socket
        original_import = ms_module.__import__ if hasattr(ms_module, "__import__") else None
        fake_socket = _FakeSocketModule()

        try:
            ms_module.socket = fake_socket
            ms_module.__import__ = _fake_import

            diag = svc.connectivity_diag()

            self.assertTrue(diag["resolve_ok"])
            self.assertTrue(diag["connect_ok"])
            self.assertIsNone(diag["resolve_error"])
            self.assertIsNone(diag["connect_error"])
            self.assertEqual(diag["resolve_addr"], ("192.168.1.99", 8765))
            self.assertEqual(
                diag["ifconfig"],
                ("192.168.1.20", "255.255.255.0", "192.168.1.1", "8.8.8.8"),
            )
            self.assertEqual(len(fake_socket.created), 1)
            self.assertTrue(fake_socket.created[0].closed)
            self.assertEqual(fake_socket.created[0].connected_addr, ("192.168.1.99", 8765))
        finally:
            ms_module.socket = original_socket
            if original_import is None:
                delattr(ms_module, "__import__")
            else:
                ms_module.__import__ = original_import

    def test_connectivity_diag_resolve_failure(self):
        import services.metrics_service as ms_module

        cfg = {"metrics": {"pc_url": "http://pc-host.local:8765/api/system/metrics"}}
        svc = MetricsService(cfg)

        class _FakeSocketModule:
            SOCK_STREAM = 1
            AF_INET = 2

            def getaddrinfo(self, _host, _port, *_args):
                raise OSError("dns failed")

            def socket(self, *_args):
                raise AssertionError("socket() must not be called on resolve failure")

        class _FakeNetwork:
            STA_IF = 0

            @staticmethod
            def WLAN(_if_id):
                raise RuntimeError("wifi unavailable")

        def _fake_import(name, *_args, **_kwargs):
            if name == "network":
                return _FakeNetwork
            raise ImportError(name)

        original_socket = ms_module.socket
        original_import = ms_module.__import__ if hasattr(ms_module, "__import__") else None

        try:
            ms_module.socket = _FakeSocketModule()
            ms_module.__import__ = _fake_import

            diag = svc.connectivity_diag()

            self.assertFalse(diag["resolve_ok"])
            self.assertFalse(diag["connect_ok"])
            self.assertEqual(diag["resolve_error"], "dns failed")
            self.assertEqual(diag["connect_error"], "resolve failed")
        finally:
            ms_module.socket = original_socket
            if original_import is None:
                delattr(ms_module, "__import__")
            else:
                ms_module.__import__ = original_import

    def test_connectivity_diag_connect_failure_closes_socket(self):
        import services.metrics_service as ms_module

        cfg = {"metrics": {"pc_url": "http://pc-host.local:8765/api/system/metrics"}}
        svc = MetricsService(cfg)

        class _FakeSocketInstance:
            def __init__(self):
                self.closed = False

            def settimeout(self, _timeout):
                return None

            def connect(self, _addr):
                raise OSError("connect refused")

            def close(self):
                self.closed = True

        class _FakeSocketModule:
            SOCK_STREAM = 1
            AF_INET = 2

            def __init__(self):
                self.created = []

            def getaddrinfo(self, _host, port, *_args):
                return [(None, None, None, None, ("192.168.1.99", port))]

            def socket(self, _af, _sock_type):
                inst = _FakeSocketInstance()
                self.created.append(inst)
                return inst

        class _FakeNetwork:
            STA_IF = 0

            @staticmethod
            def WLAN(_if_id):
                raise RuntimeError("wifi unavailable")

        def _fake_import(name, *_args, **_kwargs):
            if name == "network":
                return _FakeNetwork
            raise ImportError(name)

        original_socket = ms_module.socket
        original_import = ms_module.__import__ if hasattr(ms_module, "__import__") else None
        fake_socket = _FakeSocketModule()

        try:
            ms_module.socket = fake_socket
            ms_module.__import__ = _fake_import

            diag = svc.connectivity_diag()

            self.assertTrue(diag["resolve_ok"])
            self.assertFalse(diag["connect_ok"])
            self.assertEqual(diag["connect_error"], "connect refused")
            self.assertEqual(len(fake_socket.created), 1)
            self.assertTrue(fake_socket.created[0].closed)
        finally:
            ms_module.socket = original_socket
            if original_import is None:
                delattr(ms_module, "__import__")
            else:
                ms_module.__import__ = original_import


if __name__ == "__main__":
    unittest.main()
