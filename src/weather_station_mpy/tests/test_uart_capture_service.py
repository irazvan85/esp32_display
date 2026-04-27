"""Tests for uart_capture_service — snapshot building and command parsing."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeState:
    """Minimal AppState stand-in for testing."""
    page = 2
    enabled_pages = [0, 1, 2, 3, 4, 5]
    wifi_online = True
    time_synced = True
    web_ready = False
    weather_error = ""
    net_error_streak = 0
    weather = {
        "valid": True, "temp_c": 12.3, "feels_like_c": 10.1,
        "humidity": 72, "condition": "Rain", "condition_id": 500,
        "wind_ms": 4.2,
    }
    forecast = [
        {"date": "2026-04-28", "day": "Tue", "temp_min": 8.0, "temp_max": 15.0,
         "humidity": 70, "condition": "Clouds", "condition_id": 801},
    ]
    weather_trend = []
    solar = {"valid": False, "generation_w": 0.0, "grid_w": 0.0, "battery_soc": 0.0}
    metrics = {
        "valid": True, "cpu_pct": 45.0, "ram_pct": 62.0, "disk_pct": 88.0,
        "temp_c": 55.0, "gpu_pct": None, "gpu_temp_c": None, "uptime_s": 3600, "ts": 0,
    }
    esp_status = {"ram_free_kb": 65, "cpu_mhz": 160, "rssi": -55, "ip": "192.168.1.31"}
    last_weather_fetch_ms = 10000
    last_metrics_fetch_ms = 5000
    last_solar_fetch_ms = 0


class TestBuildSnapshot(unittest.TestCase):
    def setUp(self):
        from services.uart_capture_service import _build_snapshot
        self._build_snapshot = _build_snapshot

    def test_required_keys_present(self):
        data = self._build_snapshot(_FakeState())
        required = {
            "page", "enabled_pages", "wifi_online", "time_synced",
            "weather_error", "weather", "forecast", "metrics", "esp_status",
        }
        for key in required:
            self.assertIn(key, data, f"Missing key: {key}")

    def test_page_value_correct(self):
        data = self._build_snapshot(_FakeState())
        self.assertEqual(data["page"], 2)

    def test_wifi_online_is_bool(self):
        data = self._build_snapshot(_FakeState())
        self.assertIsInstance(data["wifi_online"], bool)
        self.assertTrue(data["wifi_online"])

    def test_weather_valid_preserved(self):
        data = self._build_snapshot(_FakeState())
        self.assertTrue(data["weather"]["valid"])
        self.assertAlmostEqual(data["weather"]["temp_c"], 12.3)

    def test_metrics_cpu_preserved(self):
        data = self._build_snapshot(_FakeState())
        self.assertAlmostEqual(data["metrics"]["cpu_pct"], 45.0)

    def test_forecast_list_preserved(self):
        data = self._build_snapshot(_FakeState())
        self.assertEqual(len(data["forecast"]), 1)
        self.assertEqual(data["forecast"][0]["day"], "Tue")

    def test_net_error_streak_included(self):
        data = self._build_snapshot(_FakeState())
        self.assertEqual(data["net_error_streak"], 0)

    def test_snapshot_is_json_serialisable(self):
        """The snapshot dict must not raise when serialised."""
        import json
        data = self._build_snapshot(_FakeState())
        # Should not raise
        serialised = json.dumps(data)
        self.assertIn("wifi_online", serialised)


class TestSafeHelper(unittest.TestCase):
    def setUp(self):
        from services.uart_capture_service import _safe
        self._safe = _safe

    def test_safe_returns_value_for_simple_types(self):
        self.assertEqual(self._safe(42), 42)
        self.assertEqual(self._safe("hello"), "hello")
        self.assertEqual(self._safe([1, 2]), [1, 2])

    def test_safe_returns_default_for_non_serialisable(self):
        class Unserializable:
            pass
        result = self._safe(Unserializable(), default="fallback")
        self.assertEqual(result, "fallback")

    def test_safe_returns_none_default(self):
        class Bad:
            pass
        result = self._safe(Bad())
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
