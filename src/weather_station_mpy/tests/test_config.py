"""Tests for config/store.py — merging, cloning, validation."""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.store import (
    ConfigNotReadyError,
    _clone,
    _merge_defaults,
    _is_placeholder,
    _get_path,
    _validate,
)


class TestClone(unittest.TestCase):
    def test_deep_copy_dict(self):
        original = {"a": {"b": 1}}
        cloned = _clone(original)
        cloned["a"]["b"] = 99
        self.assertEqual(original["a"]["b"], 1, "clone should be a deep copy")

    def test_deep_copy_list(self):
        original = [1, [2, 3]]
        cloned = _clone(original)
        cloned[1][0] = 99
        self.assertEqual(original[1][0], 2)

    def test_scalar_passthrough(self):
        self.assertEqual(_clone(42), 42)
        self.assertEqual(_clone("hello"), "hello")
        self.assertIsNone(_clone(None))


class TestIsPlaceholder(unittest.TestCase):
    def test_your_prefix(self):
        self.assertTrue(_is_placeholder("your_wifi_ssid"))
        self.assertTrue(_is_placeholder("YOUR_API_KEY"))

    def test_changeme(self):
        self.assertTrue(_is_placeholder("changeme"))
        self.assertTrue(_is_placeholder("CHANGEME"))

    def test_empty_string(self):
        self.assertTrue(_is_placeholder(""))
        self.assertTrue(_is_placeholder("   "))

    def test_non_placeholder(self):
        self.assertFalse(_is_placeholder("MyRealSSID"))
        self.assertFalse(_is_placeholder("abc123key"))

    def test_non_string(self):
        self.assertFalse(_is_placeholder(42))
        self.assertFalse(_is_placeholder(None))
        self.assertFalse(_is_placeholder(True))


class TestGetPath(unittest.TestCase):
    def test_nested_key(self):
        cfg = {"wifi": {"ssid": "Test"}}
        self.assertEqual(_get_path(cfg, "wifi.ssid"), "Test")

    def test_missing_key_returns_none(self):
        self.assertIsNone(_get_path({}, "wifi.ssid"))
        self.assertIsNone(_get_path({"wifi": {}}, "wifi.ssid"))

    def test_top_level_key(self):
        cfg = {"display": {"backlight_on": True}}
        self.assertEqual(_get_path(cfg, "display"), {"backlight_on": True})


class TestMergeDefaults(unittest.TestCase):
    def test_user_value_overrides_default(self):
        defaults = {"wifi": {"ssid": "default_ssid", "timeout": 5000}}
        user = {"wifi": {"ssid": "my_ssid"}}
        merged = _merge_defaults(user, defaults)
        self.assertEqual(merged["wifi"]["ssid"], "my_ssid")
        self.assertEqual(merged["wifi"]["timeout"], 5000)

    def test_extra_user_keys_preserved(self):
        defaults = {"a": 1}
        user = {"a": 1, "b": 2}
        merged = _merge_defaults(user, defaults)
        self.assertEqual(merged["b"], 2)

    def test_non_dict_user_cfg_returns_defaults(self):
        defaults = {"a": 1}
        merged = _merge_defaults(None, defaults)
        self.assertEqual(merged, {"a": 1})

    def test_nested_merge(self):
        defaults = {"wifi": {"ssid": "d", "pass": "p", "timeout": 10}}
        user = {"wifi": {"ssid": "mine"}}
        merged = _merge_defaults(user, defaults)
        self.assertEqual(merged["wifi"]["ssid"], "mine")
        self.assertEqual(merged["wifi"]["pass"], "p")
        self.assertEqual(merged["wifi"]["timeout"], 10)


class TestValidate(unittest.TestCase):
    def _valid_cfg(self):
        return {
            "wifi": {"ssid": "MyNet", "password": "secret123"},
            "weather": {"api_key": "abc123key", "city": "London", "country": "GB"},
            "solar": {"enabled": False},
            "metrics": {"enabled": False},
            "ui": {"theme": "retro", "enabled_pages": [0, 1, 2, 3, 4, 5]},
        }

    def test_valid_config_passes(self):
        _validate(self._valid_cfg())  # should not raise

    def test_missing_ssid_raises(self):
        cfg = self._valid_cfg()
        cfg["wifi"]["ssid"] = "your_wifi_ssid"
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_placeholder_api_key_raises(self):
        cfg = self._valid_cfg()
        cfg["weather"]["api_key"] = "your_openweathermap_api_key"
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_empty_city_raises(self):
        cfg = self._valid_cfg()
        cfg["weather"]["city"] = ""
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_solar_enabled_with_placeholder_raises(self):
        cfg = self._valid_cfg()
        cfg["solar"] = {
            "enabled": True,
            "app_id": "your_app_id",
            "app_secret": "secret",
            "email": "test@test.com",
            "pass_sha256": "abc",
            "station_id": 1,
        }
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_solar_enabled_with_valid_values_passes(self):
        cfg = self._valid_cfg()
        cfg["solar"] = {
            "enabled": True,
            "app_id": "real_id",
            "app_secret": "real_secret",
            "email": "user@domain.com",
            "pass_sha256": "deadbeef",
            "station_id": 12345,
        }
        _validate(cfg)  # should not raise

    def test_invalid_theme_raises(self):
        cfg = self._valid_cfg()
        cfg["ui"] = {"theme": "nope", "enabled_pages": [0, 1, 2]}
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_non_list_enabled_pages_raises(self):
        cfg = self._valid_cfg()
        cfg["ui"] = {"theme": "retro", "enabled_pages": "0,1,2"}
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_empty_enabled_pages_raises(self):
        cfg = self._valid_cfg()
        cfg["ui"] = {"theme": "retro", "enabled_pages": []}
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_page_out_of_range_raises(self):
        cfg = self._valid_cfg()
        cfg["ui"] = {"theme": "retro", "enabled_pages": [0, 6]}
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_non_int_page_raises(self):
        cfg = self._valid_cfg()
        cfg["ui"] = {"theme": "retro", "enabled_pages": [0, "1"]}
        with self.assertRaises(ConfigNotReadyError):
            _validate(cfg)

    def test_valid_ui_config_passes(self):
        cfg = self._valid_cfg()
        cfg["ui"] = {"theme": "light", "enabled_pages": [0, 2, 4]}
        _validate(cfg)


if __name__ == "__main__":
    unittest.main()
