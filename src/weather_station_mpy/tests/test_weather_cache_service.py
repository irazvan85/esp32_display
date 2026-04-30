"""Tests for WeatherCacheService persistence and sanitization behavior."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.weather_cache_service import WeatherCacheService


class TestWeatherCacheServiceLoad(unittest.TestCase):
    def test_load_missing_file_returns_none(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            tmp_path = handle.name
        os.unlink(tmp_path)

        svc = WeatherCacheService(tmp_path)
        self.assertIsNone(svc.load())

    def test_load_corrupt_file_returns_none(self):
        with tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8") as handle:
            tmp_path = handle.name
            handle.write("{not-json")
        try:
            svc = WeatherCacheService(tmp_path)
            self.assertIsNone(svc.load())
        finally:
            os.unlink(tmp_path)


class TestWeatherCacheServiceSaveValidation(unittest.TestCase):
    def test_save_rejects_non_dict_weather_payload(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            tmp_path = handle.name
        try:
            svc = WeatherCacheService(tmp_path)
            self.assertFalse(svc.save(weather=None, forecast=[], trend=[]))
            self.assertFalse(svc.save(weather="bad", forecast=[], trend=[]))
        finally:
            os.unlink(tmp_path)

    def test_save_rejects_weather_without_valid_true(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            tmp_path = handle.name
        try:
            svc = WeatherCacheService(tmp_path)
            weather = {
                "valid": False,
                "temp_c": 12.3,
                "feels_like_c": 11.8,
                "humidity": 70,
                "condition": "Clouds",
                "condition_id": 801,
                "wind_ms": 3.4,
                "fetched_ms": 123,
            }
            self.assertFalse(svc.save(weather=weather, forecast=[], trend=[]))
        finally:
            os.unlink(tmp_path)


class TestWeatherCacheServiceRoundTrip(unittest.TestCase):
    def test_save_and_load_roundtrip_is_sanitized_and_capped(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            tmp_path = handle.name
        try:
            svc = WeatherCacheService(tmp_path)

            weather_in = {
                "valid": True,
                "temp_c": "21.75",
                "feels_like_c": "20.25",
                "humidity": "66",
                "condition": "VeryLongConditionNameThatExceedsTwentyFourChars",
                "condition_id": "802",
                "wind_ms": "4.8",
                "fetched_ms": 999_999,
            }

            forecast_in = [
                {
                    "date": "2026-04-2%d-extra" % idx,
                    "day": "Weekday%d-verylong" % idx,
                    "temp_min": "1%d.5" % idx,
                    "temp_max": "2%d.5" % idx,
                    "humidity": "5%d" % idx,
                    "condition": "ConditionLabelTooLong%d" % idx,
                    "condition_id": "80%d" % idx,
                }
                for idx in range(7)
            ]

            trend_in = [
                {
                    "hour": str(idx),
                    "temp_c": "%d.25" % idx,
                    "precip_mm": "%d.75" % idx,
                }
                for idx in range(11)
            ]

            self.assertTrue(svc.save(weather=weather_in, forecast=forecast_in, trend=trend_in))
            loaded = svc.load()

            self.assertIsNotNone(loaded)
            weather = loaded["weather"]
            forecast = loaded["forecast"]
            trend = loaded["trend"]

            self.assertTrue(weather["valid"])
            self.assertAlmostEqual(weather["temp_c"], 21.75)
            self.assertAlmostEqual(weather["feels_like_c"], 20.25)
            self.assertEqual(weather["humidity"], 66)
            self.assertEqual(weather["condition"], weather_in["condition"][:24])
            self.assertEqual(weather["condition_id"], 802)
            self.assertAlmostEqual(weather["wind_ms"], 4.8)
            # fetched_ms is intentionally reset on save/load sanitization.
            self.assertEqual(weather["fetched_ms"], 0)

            self.assertEqual(len(forecast), 5)
            for day in forecast:
                self.assertLessEqual(len(day["date"]), 10)
                self.assertLessEqual(len(day["day"]), 8)
                self.assertLessEqual(len(day["condition"]), 16)

            self.assertEqual(len(trend), 8)
            for point in trend:
                self.assertIsInstance(point["hour"], int)
                self.assertIsInstance(point["temp_c"], float)
                self.assertIsInstance(point["precip_mm"], float)

            with open(tmp_path, "r", encoding="utf-8") as handle:
                on_disk = json.load(handle)
            self.assertEqual(on_disk.get("v"), 1)
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()