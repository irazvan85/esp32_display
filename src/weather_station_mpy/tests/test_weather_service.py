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


class TestWeatherServiceForecastBundle(unittest.TestCase):
    def setUp(self):
        from services.weather_service import WeatherService
        self.WeatherService = WeatherService
        self.cfg = {
            "weather": {
                "api_key": "testkey",
                "city": "London",
                "country": "GB",
            }
        }

    @staticmethod
    def _forecast_entry(date_str, time_str, temp_c, rain_3h=0.0, snow_3h=0.0):
        return {
            "dt_txt": "%s %s" % (date_str, time_str),
            "main": {
                "temp": temp_c,
                "temp_min": temp_c - 1.0,
                "temp_max": temp_c + 1.0,
                "humidity": 60,
            },
            "weather": [{"main": "Clouds", "id": 801}],
            "rain": {"3h": rain_3h},
            "snow": {"3h": snow_3h},
        }

    def test_fetch_forecast_bundle_trend_shape_precip_cap_and_first_day(self):
        entries = []
        for i in range(10):
            hour = (i * 3) % 24
            entries.append(
                self._forecast_entry(
                    "2026-04-24",
                    "%02d:00:00" % hour,
                    10.0 + i,
                    rain_3h=0.2 * i,
                    snow_3h=0.1 * i,
                )
            )

        # Second day should not appear in trend output.
        entries.append(self._forecast_entry("2026-04-25", "00:00:00", 99.0, rain_3h=9.0, snow_3h=9.0))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"list": entries}

        import services.weather_service as ws_module
        original_requests = ws_module.requests
        try:
            ws_module.requests = MagicMock()
            ws_module.requests.get.return_value = mock_response

            svc = self.WeatherService(self.cfg)
            daily, trend = svc.fetch_forecast_bundle()

            self.assertIsInstance(daily, list)
            self.assertIsInstance(trend, list)
            self.assertEqual(len(trend), 8)

            for point in trend:
                self.assertEqual(set(point.keys()), {"hour", "temp_c", "precip_mm"})

            # rain + snow accumulation is used.
            self.assertAlmostEqual(trend[3]["precip_mm"], (0.2 * 3) + (0.1 * 3))

            # Trend is only extracted from the first forecast date.
            self.assertLessEqual(max(p["hour"] for p in trend), 23)
            self.assertNotIn(99.0, [p["temp_c"] for p in trend])
        finally:
            ws_module.requests = original_requests

    def test_fetch_forecast_compat_returns_daily_list_shape(self):
        entries = [
            self._forecast_entry("2026-04-24", "06:00:00", 12.0),
            self._forecast_entry("2026-04-24", "12:00:00", 14.0),
            self._forecast_entry("2026-04-25", "06:00:00", 16.0),
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"list": entries}

        import services.weather_service as ws_module
        original_requests = ws_module.requests
        try:
            ws_module.requests = MagicMock()
            ws_module.requests.get.return_value = mock_response

            svc = self.WeatherService(self.cfg)
            daily = svc.fetch_forecast()

            self.assertIsInstance(daily, list)
            self.assertGreaterEqual(len(daily), 1)
            required_keys = {
                "date",
                "day",
                "temp_min",
                "temp_max",
                "humidity",
                "condition",
                "condition_id",
            }
            self.assertEqual(set(daily[0].keys()), required_keys)
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
