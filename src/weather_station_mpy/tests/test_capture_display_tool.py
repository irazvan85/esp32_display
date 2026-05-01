"""Tests for tools/capture_display.py page sweep helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest


class _FakeSerial:
    def __init__(self, lines):
        self._lines = [
            line if isinstance(line, bytes) else (str(line) + "\r\n").encode("utf-8")
            for line in lines
        ]
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def reset_input_buffer(self):
        return None

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        return None

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


def _load_capture_display_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "tools" / "capture_display.py"
    spec = importlib.util.spec_from_file_location("capture_display", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_trigger_snapshot_parses_payload(monkeypatch):
    module = _load_capture_display_module()
    fake = _FakeSerial(
        [
            "[noise] hello",
            ">>SNAP_START",
            '{"page":2,"enabled_pages":[0,1,2],"weather":{"valid":true}}',
            ">>SNAP_END",
        ]
    )

    monkeypatch.setattr(module.serial, "Serial", lambda *args, **kwargs: fake)

    data = module.trigger_snapshot("COM13", baud=115200, timeout_s=1)

    assert data["page"] == 2
    assert fake.writes == [b"!SNAP\r\n"]


def test_send_command_returns_ok_ack():
    module = _load_capture_display_module()
    fake = _FakeSerial([">>CMD_OK PAGE 3"])

    ok, ack = module.send_command(fake, "!PAGE 3", timeout_s=1)

    assert ok is True
    assert ack == ">>CMD_OK PAGE 3"
    assert fake.writes == [b"!PAGE 3\r\n"]


def test_render_html_includes_renderer_payload(tmp_path):
    module = _load_capture_display_module()
    out_file = tmp_path / "single_snapshot.html"

    payload = {
        "page": 0,
        "wifi_online": True,
        "time_synced": True,
        "weather": {"valid": False},
        "forecast": [],
        "metrics": {"valid": False},
        "solar": {"valid": False},
        "esp_status": {},
        "display_capture": {
            "source": "display_render",
            "page": 2,
            "wifi_online": True,
            "time_synced": True,
            "visible_text": ["Tue +8/+15 Clouds", "no wx", "[3/6]"],
            "details": {"status_age_label": "no wx"},
        },
    }

    module.render_html(payload, out_file)
    content = out_file.read_text(encoding="utf-8")

    assert "Rendered Display Data (actual on-screen fields)" in content
    assert "Tue +8/+15 Clouds" in content
    assert "Source: display_render" in content


def test_capture_page_sweep_captures_each_enabled_page(monkeypatch):
    module = _load_capture_display_module()

    lines = [
        ">>SNAP_START",
        '{"page":0,"enabled_pages":[0,1,2],"weather":{"valid":false},"display_capture":{"page":0,"visible_text":["[1/6]"]}}',
        ">>SNAP_END",
        ">>CMD_OK PAGE 0",
        ">>SNAP_START",
        '{"page":0,"enabled_pages":[0,1,2],"weather":{"valid":false},"display_capture":{"page":0,"visible_text":["[1/6]"]}}',
        ">>SNAP_END",
        ">>CMD_OK PAGE 1",
        ">>SNAP_START",
        '{"page":1,"enabled_pages":[0,1,2],"weather":{"valid":false},"display_capture":{"page":1,"visible_text":["[2/6]"]}}',
        ">>SNAP_END",
        ">>CMD_OK PAGE 2",
        ">>SNAP_START",
        '{"page":2,"enabled_pages":[0,1,2],"weather":{"valid":false},"display_capture":{"page":2,"visible_text":["[3/6]"]}}',
        ">>SNAP_END",
    ]
    fake = _FakeSerial(lines)

    monkeypatch.setattr(module.serial, "Serial", lambda *args, **kwargs: fake)

    result = module.capture_page_sweep("COM13", baud=115200, timeout_s=1, settle_ms=0)

    assert result["error"] == ""
    assert result["enabled_pages"] == [0, 1, 2]
    assert len(result["captures"]) == 3
    assert all(item["ok"] for item in result["captures"])
    assert [item["observed_page"] for item in result["captures"]] == [0, 1, 2]
    assert [item["observed_source"] for item in result["captures"]] == ["display_capture", "display_capture", "display_capture"]

    command_writes = [wire.decode("utf-8").strip() for wire in fake.writes]
    assert command_writes == [
        "!SNAP",
        "!PAGE 0",
        "!SNAP",
        "!PAGE 1",
        "!SNAP",
        "!PAGE 2",
        "!SNAP",
    ]


def test_main_all_pages_cli_success(monkeypatch, tmp_path):
    module = _load_capture_display_module()

    lines = [
        ">>SNAP_START",
        '{"page":0,"enabled_pages":[0,1],"weather":{"valid":false}}',
        ">>SNAP_END",
        ">>CMD_OK PAGE 0",
        ">>SNAP_START",
        '{"page":0,"enabled_pages":[0,1],"weather":{"valid":false}}',
        ">>SNAP_END",
        ">>CMD_OK PAGE 1",
        ">>SNAP_START",
        '{"page":1,"enabled_pages":[0,1],"weather":{"valid":false}}',
        ">>SNAP_END",
    ]
    fake = _FakeSerial(lines)

    out_file = tmp_path / "menu_report.html"
    monkeypatch.setattr(module.serial, "Serial", lambda *args, **kwargs: fake)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "capture_display.py",
            "--port",
            "COM13",
            "--all-pages",
            "--settle-ms",
            "0",
            "--timeout",
            "1",
            "--output",
            str(out_file),
        ],
    )

    module.main()

    content = out_file.read_text(encoding="utf-8")
    assert "ESP32 Menu Sweep Report" in content
    assert "Pass: 2/2" in content


def test_main_all_pages_cli_failure_exits_nonzero(monkeypatch, tmp_path):
    module = _load_capture_display_module()

    lines = [
        ">>SNAP_START",
        '{"page":0,"enabled_pages":[0,1],"weather":{"valid":false}}',
        ">>SNAP_END",
        ">>CMD_OK PAGE 0",
        ">>SNAP_START",
        '{"page":0,"enabled_pages":[0,1],"weather":{"valid":false}}',
        ">>SNAP_END",
        ">>CMD_OK PAGE 1",
        ">>SNAP_START",
        '{"page":0,"enabled_pages":[0,1],"weather":{"valid":false}}',
        ">>SNAP_END",
    ]
    fake = _FakeSerial(lines)

    out_file = tmp_path / "menu_report_fail.html"
    monkeypatch.setattr(module.serial, "Serial", lambda *args, **kwargs: fake)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "capture_display.py",
            "--port",
            "COM13",
            "--all-pages",
            "--settle-ms",
            "0",
            "--timeout",
            "1",
            "--output",
            str(out_file),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        module.main()

    assert exc_info.value.code == 1
    assert out_file.exists()


def test_frame_dump_parses_chunked_hex_payload():
    module = _load_capture_display_module()
    fake = _FakeSerial(
        [
            ">>FRAME_START",
            '{"width":2,"height":1,"format":"RGB565","byte_len":4}',
            ">>FRAME_CHUNK 0 0000ffff",
            ">>FRAME_END",
        ]
    )

    frame, error = module._frame_dump_from_open_serial(fake, timeout_s=1)

    assert error == ""
    assert frame is not None
    assert frame["meta"]["width"] == 2
    assert frame["meta"]["height"] == 1
    assert frame["bytes"] == b"\x00\x00\xff\xff"
    assert fake.writes == [b"!FRAME DUMP\r\n"]


def test_save_rgb565_ppm_writes_expected_header_and_size(tmp_path):
    module = _load_capture_display_module()
    ppm_path = tmp_path / "frame.ppm"

    frame_payload = {
        "meta": {"width": 2, "height": 1, "format": "RGB565", "byte_len": 4},
        "bytes": b"\x00\x00\xff\xff",
    }

    module._save_rgb565_ppm(frame_payload, ppm_path)
    payload = ppm_path.read_bytes()

    assert payload.startswith(b"P6\n2 1\n255\n")
    # Header (11 bytes) + 2 pixels * 3 bytes RGB = 17
    assert len(payload) == 17
