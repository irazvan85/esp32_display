"""Host-side tests for solarmann/pc_metrics_api.py.

These tests are deterministic and monkeypatch all external dependencies.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_pc_metrics_api_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "solarmann" / "pc_metrics_api.py"
    spec = importlib.util.spec_from_file_location("pc_metrics_api", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_lhm_parse_value_extracts_numeric_values():
    module = _load_pc_metrics_api_module()

    assert module._lhm_parse_value("45.0 C") == 45.0
    assert module._lhm_parse_value("-12.5 %") == -12.5
    assert module._lhm_parse_value("GPU Core: +99") == 99.0
    assert module._lhm_parse_value("N/A") is None


def test_extract_sensors_from_tree_flattens_nested_children():
    module = _load_pc_metrics_api_module()

    root = {
        "Text": "Sensor",
        "Children": [
            {
                "Text": "CPU",
                "Children": [
                    {"SensorId": "/cpu/0/temp/0", "Type": "Temperature", "Value": "52 C"},
                    {"Text": "Not a sensor", "Children": []},
                ],
            },
            {
                "Text": "GPU",
                "Children": [
                    {"SensorId": "/gpu-nvidia/0/load/0", "Type": "Load", "Value": "88 %"},
                ],
            },
        ],
    }

    sensors = module._extract_sensors_from_tree(root)

    assert [s["SensorId"] for s in sensors] == [
        "/cpu/0/temp/0",
        "/gpu-nvidia/0/load/0",
    ]


def test_pick_gpu_pct_prefers_core_load_over_memory_load(monkeypatch):
    module = _load_pc_metrics_api_module()

    sensors = [
        {
            "Type": "Load",
            "SensorId": "/gpu-nvidia/0/load/1",
            "Text": "GPU Memory",
            "Value": "31.1 %",
        },
        {
            "Type": "Load",
            "SensorId": "/gpu-nvidia/0/load/0",
            "Text": "GPU Core",
            "Value": "77.7 %",
        },
    ]

    monkeypatch.setattr(module, "_lhm_get_sensors", lambda _url: sensors)

    assert module._pick_gpu_pct("http://localhost:8085/data.json") == 77.7


def test_collect_metrics_returns_expected_schema_and_types(monkeypatch):
    module = _load_pc_metrics_api_module()

    monkeypatch.setattr(module.psutil, "virtual_memory", lambda: SimpleNamespace(percent=61.24))
    monkeypatch.setattr(module.psutil, "disk_usage", lambda _path: SimpleNamespace(percent=82.85))
    monkeypatch.setattr(module.psutil, "cpu_percent", lambda interval=None: 12.34)
    monkeypatch.setattr(module.psutil, "boot_time", lambda: 900.0)
    monkeypatch.setattr(module, "_pick_temperature_c", lambda _url: 55.5)
    monkeypatch.setattr(module, "_pick_gpu_temp_c", lambda _url: 66.6)
    monkeypatch.setattr(module, "_pick_gpu_pct", lambda _url: 44.4)
    monkeypatch.setattr(module, "_lhm_is_available", lambda: True)
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)

    payload = module._collect_metrics("C:\\", "http://127.0.0.1:8085/data.json")

    assert set(payload.keys()) == {
        "valid",
        "cpu_pct",
        "ram_pct",
        "disk_pct",
        "temp_c",
        "gpu_temp_c",
        "gpu_pct",
        "lhm_available",
        "uptime_s",
        "ts",
    }
    assert payload["valid"] is True
    assert isinstance(payload["valid"], bool)
    assert isinstance(payload["cpu_pct"], float)
    assert isinstance(payload["ram_pct"], float)
    assert isinstance(payload["disk_pct"], float)
    assert isinstance(payload["temp_c"], float)
    assert isinstance(payload["gpu_temp_c"], float)
    assert isinstance(payload["gpu_pct"], float)
    assert isinstance(payload["lhm_available"], bool)
    assert isinstance(payload["uptime_s"], int)
    assert isinstance(payload["ts"], int)

    assert payload["cpu_pct"] == 12.3
    assert payload["ram_pct"] == 61.2
    assert payload["disk_pct"] == 82.8
    assert payload["uptime_s"] == 100
    assert payload["ts"] == 1000