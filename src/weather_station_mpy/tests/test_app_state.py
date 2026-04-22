"""Tests for app_state.py — initial values and dirty-flag mechanics."""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app_state import AppState


class TestAppStateInit(unittest.TestCase):
    def setUp(self):
        self.state = AppState()

    def test_initial_page(self):
        self.assertEqual(self.state.page, 0)

    def test_dirty_flags_set_on_init(self):
        self.assertTrue(self.state.page_dirty)
        self.assertTrue(self.state.weather_dirty)
        self.assertTrue(self.state.forecast_dirty)
        self.assertTrue(self.state.solar_dirty)
        self.assertTrue(self.state.metrics_dirty)
        self.assertTrue(self.state.clock_dirty)
        self.assertTrue(self.state.date_dirty)
        self.assertTrue(self.state.esp_status_dirty)

    def test_initial_connectivity_state(self):
        self.assertFalse(self.state.wifi_online)
        self.assertFalse(self.state.time_synced)

    def test_initial_weather_structure(self):
        w = self.state.weather
        self.assertFalse(w["valid"])
        self.assertIn("temp_c", w)
        self.assertIn("condition", w)
        self.assertIn("condition_id", w)

    def test_initial_metrics_structure(self):
        m = self.state.metrics
        self.assertFalse(m["valid"])
        self.assertIsNone(m["temp_c"])
        self.assertIsNone(m["gpu_pct"])

    def test_initial_esp_status_empty(self):
        self.assertIsInstance(self.state.esp_status, dict)
        self.assertEqual(len(self.state.esp_status), 0)

    def test_metrics_subpage_initial(self):
        self.assertEqual(self.state.metrics_subpage, 0)


class TestMarkAllDirty(unittest.TestCase):
    def test_mark_all_dirty_sets_all_flags(self):
        state = AppState()
        # Clear all flags first
        state.page_dirty = False
        state.status_dirty = False
        state.weather_dirty = False
        state.forecast_dirty = False
        state.solar_dirty = False
        state.metrics_dirty = False
        state.clock_dirty = False
        state.date_dirty = False
        state.esp_status_dirty = False

        state.mark_all_dirty()

        self.assertTrue(state.page_dirty)
        self.assertTrue(state.status_dirty)
        self.assertTrue(state.weather_dirty)
        self.assertTrue(state.forecast_dirty)
        self.assertTrue(state.solar_dirty)
        self.assertTrue(state.metrics_dirty)
        self.assertTrue(state.clock_dirty)
        self.assertTrue(state.date_dirty)
        self.assertTrue(state.esp_status_dirty)


if __name__ == "__main__":
    unittest.main()
