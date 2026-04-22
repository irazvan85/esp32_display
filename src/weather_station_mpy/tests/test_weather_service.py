"""Tests for WeatherService — parsing, aggregation, day naming.

HTTP calls are mocked; no network access required.
"""

import unittest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestWeatherServiceDayName(unittest.TestCase):
    def setUp(self):
        from services.weather_service import WeatherService
        self.WeatherService = WeatherService

    def test_known_dates(self):
        # 2025-01-06 is a Monday
        self.assertEqual(self.WeatherService._day_name("2025-01-06"), "Mon")
        # 2025-01-12 is a Sunday
        self.assertEqual(self.WeatherService._day_name("2025-01-12"), "Sun")
        # 2026-04-23 is a Thursday
        self.assertEqual(self.WeatherService._day_name("2026-04-23"), "Thu")

    def test_returns_three_char_string(self):
        day = self.WeatherService._day_name("2026-01-01")
        self.assertEqual(len(day), 3)
        self.assertIn(day, ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"))


class TestWeatherServiceAggregate(unittest.TestCase):
    def setUp(self):
        from services.weather_service import WeatherService
        self.svc = WeatherService({})

    def _entry(self, date_str, time_str="06:00:00", temp=15.0, t_min=10.0,
               t_max=20.0, humidity=60, cond="Clouds", cond_id=801):
        return {
            "dt_txt": "%s %s" % (date_str, time_str),
            "main": {
                "temp": temp, "temp_min": t_min, "temp_max": t_max,
                "humidity": humidity,
            },
            "weather": [{"main": cond, "id": cond_id}],
        }

    def test_groups_by_date(self):
        entries = [
            self._entry("2026-04-24", "06:00:00"),
            self._entry("2026-04-24", "12:00:00"),
            self._entry("2026-04-25", "06:00:00"),
        ]
        result = self.svc._aggregate(entries)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], "2026-04-24")
        self.assertEqual(result[1]["date"], "2026-04-25")

    def test_min_max_tracking(self):
        entries = [
            self._entry("2026-04-24", temp=15.0, t_min=10.0, t_max=18.0),
            self._entry("2026-04-24", temp=20.0, t_min=13.0, t_max=25.0),
        ]
        result = self.svc._aggregate(entries)
        self.assertAlmostEqual(result[0]["temp_min"], 10.0)
        self.assertAlmostEqual(result[0]["temp_max"], 25.0)

    def test_noon_sample_preferred(self):
        entries = [
            self._entry("2026-04-24", "06:00:00", cond="Clouds", cond_id=801),
            self._entry("2026-04-24", "12:00:00", cond="Rain",   cond_id=500),
        ]
        result = self.svc._aggregate(entries)
        self.assertEqual(result[0]["condition"], "Rain")
        self.assertEqual(result[0]["condition_id"], 500)

    def test_capped_at_five_days(self):
        entries = []
        for day in range(1, 9):
            entries.append(self._entry("2026-04-%02d" % day))
        result = self.svc._aggregate(entries)
        self.assertLessEqual(len(result), 5)

    def test_result_structure(self):
        entries = [self._entry("2026-04-24")]
        result = self.svc._aggregate(entries)
        required_keys = {"date", "day", "temp_min", "temp_max", "humidity",
                         "condition", "condition_id"}
        self.assertEqual(set(result[0].keys()), required_keys)

    def test_short_dt_txt_skipped(self):
        entries = [{"dt_txt": "bad", "main": {}, "weather": [{}]}]
        result = self.svc._aggregate(entries)
        self.assertEqual(result, [])


class TestWeatherServiceFetchCurrent(unittest.TestCase):
    def setUp(self):
        from services.weather_service import WeatherService
        self.WeatherService = WeatherService

    def test_fetch_current_parses_response(self):
        cfg = {
            "weather": {
                "api_key": "testkey",
                "city": "London",
                "country": "GB",
            }
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "weather": [{"main": "Clear", "id": 800}],
            "main": {
                "temp": 18.5, "feels_like": 17.0, "humidity": 65,
            },
            "wind": {"speed": 3.2},
        }

        import services.weather_service as ws_module
        original_requests = ws_module.requests
        try:
            ws_module.requests = MagicMock()
            ws_module.requests.get.return_value = mock_response

            svc = self.WeatherService(cfg)
            result = svc.fetch_current()

            self.assertTrue(result["valid"])
            self.assertAlmostEqual(result["temp_c"], 18.5)
            self.assertAlmostEqual(result["feels_like_c"], 17.0)
            self.assertEqual(result["humidity"], 65)
            self.assertEqual(result["condition"], "Clear")
            self.assertEqual(result["condition_id"], 800)
            self.assertAlmostEqual(result["wind_ms"], 3.2)
        finally:
            ws_module.requests = original_requests

    def test_fetch_current_raises_on_http_error(self):
        cfg = {"weather": {"api_key": "k", "city": "X", "country": "Y"}}
        mock_response = MagicMock()
        mock_response.status_code = 401

        import services.weather_service as ws_module
        original_requests = ws_module.requests
        try:
            ws_module.requests = MagicMock()
            ws_module.requests.get.return_value = mock_response

            svc = self.WeatherService(cfg)
            with self.assertRaises(RuntimeError):
                svc.fetch_current()
        finally:
            ws_module.requests = original_requests


if __name__ == "__main__":
    unittest.main()
