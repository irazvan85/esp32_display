"""First-boot config persistence and validation."""

import json
import os

from config.defaults import DEFAULT_CONFIG


class ConfigNotReadyError(Exception):
    pass


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _clone(obj):
    if isinstance(obj, dict):
        out = {}
        for key, val in obj.items():
            out[key] = _clone(val)
        return out
    if isinstance(obj, list):
        return [_clone(item) for item in obj]
    return obj


def _merge_defaults(user_cfg, defaults):
    merged = _clone(defaults)
    if not isinstance(user_cfg, dict):
        return merged

    for key, val in user_cfg.items():
        if key not in merged:
            merged[key] = val
            continue

        if isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _merge_defaults(val, merged[key])
        else:
            merged[key] = val

    return merged


def _get_path(cfg, path):
    node = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _is_placeholder(value):
    if not isinstance(value, str):
        return False
    low = value.strip().lower()
    if not low:
        return True
    return ("your_" in low) or ("changeme" in low)


def _validate(cfg):
    required_paths = [
        "wifi.ssid",
        "wifi.password",
        "weather.api_key",
        "weather.city",
        "weather.country",
    ]

    for path in required_paths:
        val = _get_path(cfg, path)
        if val is None or _is_placeholder(val):
            raise ConfigNotReadyError("Missing or placeholder value: %s" % path)

    solar_enabled = bool(_get_path(cfg, "solar.enabled"))
    if solar_enabled:
        solar_required = [
            "solar.app_id",
            "solar.app_secret",
            "solar.email",
            "solar.pass_sha256",
            "solar.station_id",
        ]
        for path in solar_required:
            val = _get_path(cfg, path)
            if val is None:
                raise ConfigNotReadyError("Missing required solar value: %s" % path)
            if isinstance(val, str) and _is_placeholder(val):
                raise ConfigNotReadyError("Placeholder solar value: %s" % path)

    metrics_enabled = bool(_get_path(cfg, "metrics.enabled"))
    if metrics_enabled:
        metrics_url = _get_path(cfg, "metrics.pc_url")
        if metrics_url is None or _is_placeholder(metrics_url):
            raise ConfigNotReadyError("Missing or placeholder value: metrics.pc_url")


def write_default_config(path="config.json"):
    cfg = _clone(DEFAULT_CONFIG)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle)


def load_config(path="config.json"):
    if not _exists(path):
        write_default_config(path)
        raise ConfigNotReadyError(
            "Created %s. Fill placeholders and reboot." % path
        )

    try:
        with open(path, "r", encoding="utf-8") as handle:
            user_cfg = json.load(handle)
    except Exception as exc:
        raise ConfigNotReadyError("Invalid config JSON: %s" % exc) from exc

    cfg = _merge_defaults(user_cfg, DEFAULT_CONFIG)
    _validate(cfg)
    return cfg
