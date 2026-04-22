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


if __name__ == "__main__":
    unittest.main()
