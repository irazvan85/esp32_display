"""Tests for uart_capture_service snapshot and command helpers."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeState:
    """Minimal AppState stand-in for testing."""

    def __init__(self):
        self.page = 2
        self.enabled_pages = [0, 1, 2, 3, 4, 5]
        self.metrics_subpage = 0
        self.page_dirty = False
        self.status_dirty = False
        self.metrics_dirty = False

        self.wifi_online = True
        self.time_synced = True
        self.web_ready = False
        self.weather_error = ""
        self.net_error_streak = 0

        self.weather = {
            "valid": True,
            "temp_c": 12.3,
            "feels_like_c": 10.1,
            "humidity": 72,
            "condition": "Rain",
            "condition_id": 500,
            "wind_ms": 4.2,
        }
        self.forecast = [
            {
                "date": "2026-04-28",
                "day": "Tue",
                "temp_min": 8.0,
                "temp_max": 15.0,
                "humidity": 70,
                "condition": "Clouds",
                "condition_id": 801,
            },
        ]
        self.weather_trend = []
        self.solar = {
            "valid": False,
            "generation_w": 0.0,
            "grid_w": 0.0,
            "battery_soc": 0.0,
        }
        self.metrics = {
            "valid": True,
            "cpu_pct": 45.0,
            "ram_pct": 62.0,
            "disk_pct": 88.0,
            "temp_c": 55.0,
            "gpu_pct": None,
            "gpu_temp_c": None,
            "uptime_s": 3600,
            "ts": 0,
        }
        self.esp_status = {
            "ram_free_kb": 65,
            "cpu_mhz": 160,
            "rssi": -55,
            "ip": "192.168.1.31",
        }
        self.last_weather_fetch_ms = 10000
        self.last_metrics_fetch_ms = 5000
        self.last_solar_fetch_ms = 0
        self.display_capture = {
            "source": "display_render",
            "page": 2,
            "visible_text": ["Tue +8/+15 Clouds", "no wx", "[3/6]"],
        }
        self.pixel_capture = _FakePixelCapture()


class _FakePixelCapture:
    def __init__(self):
        self._armed = False

    def status(self):
        return {
            "supported": True,
            "armed": self._armed,
            "width": 240,
            "height": 135,
            "format": "RGB565",
        }

    def arm(self, _reason):
        self._armed = True
        return True, "capture armed"

    def disarm(self, _reason):
        self._armed = False
        return True, "capture disarmed"

    def frame_dump(self):
        if not self._armed:
            return None, None, "capture not armed"
        return ({"width": 2, "height": 1, "format": "RGB565"}, b"\x00\x00\xff\xff", "")


class TestBuildSnapshot(unittest.TestCase):
    def setUp(self):
        from services.uart_capture_service import _build_snapshot

        self._build_snapshot = _build_snapshot

    def test_required_keys_present(self):
        data = self._build_snapshot(_FakeState())
        required = {
            "page",
            "enabled_pages",
            "metrics_subpage",
            "display_capture",
            "pixel_capture",
            "local_time",
            "snapshot_ms",
            "wifi_online",
            "time_synced",
            "weather_error",
            "weather",
            "forecast",
            "metrics",
            "esp_status",
        }
        for key in required:
            self.assertIn(key, data, "Missing key: %s" % key)

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
        import json

        data = self._build_snapshot(_FakeState())
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


class TestCommandHelpers(unittest.TestCase):
    def setUp(self):
        from services.uart_capture_service import _handle_command

        self._handle_command = _handle_command

    def test_snap_command_returns_snap_action(self):
        state = _FakeState()
        action, payload = self._handle_command(state, "!SNAP")
        self.assertEqual(action, "SNAP")
        self.assertEqual(payload, "")

    def test_page_command_sets_page_and_dirty_flags(self):
        state = _FakeState()
        state.enabled_pages = [0, 2, 4]

        action, payload = self._handle_command(state, "!PAGE 4")

        self.assertEqual(action, "EMIT")
        self.assertEqual(payload, ">>CMD_OK PAGE 4")
        self.assertEqual(state.page, 4)
        self.assertTrue(state.page_dirty)
        self.assertTrue(state.status_dirty)

    def test_page_command_rejects_disabled_page(self):
        state = _FakeState()
        state.enabled_pages = [0, 1, 2]

        action, payload = self._handle_command(state, "!PAGE 4")

        self.assertEqual(action, "EMIT")
        self.assertEqual(payload, ">>CMD_ERR PAGE disabled")
        self.assertEqual(state.page, 2)

    def test_next_command_cycles_enabled_pages(self):
        state = _FakeState()
        state.page = 4
        state.enabled_pages = [0, 2, 4]

        action, payload = self._handle_command(state, "!NEXT")

        self.assertEqual(action, "EMIT")
        self.assertEqual(payload, ">>CMD_OK NEXT 0")
        self.assertEqual(state.page, 0)
        self.assertTrue(state.page_dirty)

    def test_subpage_command_sets_metrics_flags(self):
        state = _FakeState()

        action, payload = self._handle_command(state, "!SUBPAGE 1")

        self.assertEqual(action, "EMIT")
        self.assertEqual(payload, ">>CMD_OK SUBPAGE 1")
        self.assertEqual(state.metrics_subpage, 1)
        self.assertTrue(state.metrics_dirty)
        self.assertTrue(state.status_dirty)

    def test_unknown_command_returns_error_marker(self):
        state = _FakeState()
        action, payload = self._handle_command(state, "!BOGUS")
        self.assertEqual(action, "EMIT")
        self.assertEqual(payload, ">>CMD_ERR unknown command")

    def test_capture_status_reports_fields(self):
        state = _FakeState()
        action, payload = self._handle_command(state, "!CAPTURE STATUS")
        self.assertEqual(action, "EMIT")
        self.assertIn(">>CMD_OK CAPTURE STATUS", payload)
        self.assertIn("available=1", payload)
        self.assertIn("supported=1", payload)
        self.assertIn("armed=0", payload)

    def test_capture_arm_and_disarm(self):
        state = _FakeState()

        action, payload = self._handle_command(state, "!CAPTURE ARM")
        self.assertEqual(action, "EMIT")
        self.assertIn(">>CMD_OK CAPTURE ARM", payload)

        action, payload = self._handle_command(state, "!CAPTURE DISARM")
        self.assertEqual(action, "EMIT")
        self.assertIn(">>CMD_OK CAPTURE DISARM", payload)

    def test_frame_dump_command_returns_frame_action(self):
        state = _FakeState()
        action, payload = self._handle_command(state, "!FRAME DUMP")
        self.assertEqual(action, "FRAME")
        self.assertEqual(payload, "")


if __name__ == "__main__":
    unittest.main()
