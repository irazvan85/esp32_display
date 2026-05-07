"""
diag_run.py -- Orchestrator for the full ESP32 diagnostic workflow.

Usage:
    python tools/diag_run.py [--port PORT] [--baud BAUD] [--duration SECONDS]
                             [--output-dir DIR] [--reset]

Options:
    --port        Serial port (default: COM13)
    --baud        Baud rate (default: 115200)
    --duration    UART collection duration in seconds (default: 300)
    --output-dir  Directory for output files (default: tools/logs)
    --reset       Pass --reset flag through to collect_uart_log.py

Output files (all in --output-dir, timestamped YYYYMMDD_HHMMSS):
    display_status_<ts>.log   -- Plain text: page list, per-page ok/error, text summary
    display_snapshot_<ts>.html -- HTML sweep report from render_html_sweep()
    uart_raw_<ts>.log         -- Raw UART log (written by subprocess)
    issue_report_<ts>.md      -- Analysis report (written by analyze module)
"""

import argparse
import datetime
import importlib.util
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Dynamic module loader
# ---------------------------------------------------------------------------

_TOOLS_DIR = Path(__file__).resolve().parent


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# REQ-01: Display capture
# ---------------------------------------------------------------------------

PAGE_NAMES = {
    0: "Weather",
    1: "Tomorrow",
    2: "5-Day",
    3: "Solar",
    4: "PC Metrics",
    5: "ESP Status",
}


def _run_display_capture(port, baud, output_dir, ts):
    """
    Import capture_display, call capture_page_sweep(), render the HTML report,
    and write the display_status_<ts>.log file.

    Returns (status_log_path, html_path, result_dict, error_str).
    """
    status_path = output_dir / ("display_status_%s.log" % ts)
    html_path = output_dir / ("display_snapshot_%s.html" % ts)

    result = None
    error_str = ""

    try:
        cap_mod = _load_module("capture_display", _TOOLS_DIR / "capture_display.py")
    except Exception as exc:
        error_str = "failed to import capture_display: %s" % exc
        _write_status_log(status_path, port, 0, [], error_str)
        return status_path, html_path, None, error_str

    try:
        result = cap_mod.capture_page_sweep(port, baud=baud)
    except Exception as exc:
        error_str = "capture_page_sweep raised: %s" % exc
        _write_status_log(status_path, port, 0, [], error_str)
        return status_path, html_path, None, error_str

    # Write HTML report
    try:
        cap_mod.render_html_sweep(result, html_path)
    except Exception as exc:
        print("[diag] WARNING: render_html_sweep failed: %s" % exc)

    # Build display_status log
    captures = result.get("captures") or []
    global_error = result.get("error") or ""
    _write_status_log(status_path, port, len(captures), captures, global_error)

    return status_path, html_path, result, global_error


def _write_status_log(path, port, page_count, captures, global_error):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "ESP32 Diagnostic Display Status",
        "Generated: %s" % now_str,
        "Port: %s" % port,
        "==========================================",
        "Pages captured: %d" % page_count,
    ]

    for item in captures:
        page_id = item.get("requested_page", "?")
        page_name = PAGE_NAMES.get(page_id, "?") if isinstance(page_id, int) else "?"
        ok = item.get("ok", False)
        observed = item.get("observed_page", "?")
        source = item.get("observed_source", "display_capture")
        status = "OK" if ok else "ERROR"
        err = item.get("error") or ""
        lines.append(
            "Page %s (%s): %s  [observed=%s, source=%s]%s"
            % (
                page_id,
                page_name,
                status,
                observed,
                source,
                ("  -- %s" % err) if err else "",
            )
        )

        snap = item.get("snapshot")
        if snap and isinstance(snap, dict):
            dc = snap.get("display_capture")
            if isinstance(dc, dict):
                visible = dc.get("visible_text") or []
            else:
                visible = snap.get("visible_text") or []
            if visible:
                lines.append("  Rendered: %s" % ", ".join(str(v) for v in visible))

    lines.append("==========================================")
    errors_in_captures = [item for item in captures if not item.get("ok")]
    total_errors = len(errors_in_captures) + (1 if global_error else 0)
    lines.append("Errors: %d" % total_errors)
    if global_error:
        lines.append("Global error: %s" % global_error)

    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("[diag] Display status log -> %s" % path)
    except Exception as exc:
        print("[diag] WARNING: could not write display status log: %s" % exc)


# ---------------------------------------------------------------------------
# REQ-02: UART collection
# ---------------------------------------------------------------------------

def _run_uart_collection(port, baud, duration, output_dir, ts, use_reset):
    """
    Run collect_uart_log.py as a subprocess.

    Returns (uart_log_path, success_bool).
    """
    uart_log_path = output_dir / ("uart_raw_%s.log" % ts)
    collect_script = str(_TOOLS_DIR / "collect_uart_log.py")

    cmd = [
        sys.executable,
        collect_script,
        "--port", port,
        "--baud", str(baud),
        "--duration", str(duration),
        "--output", str(uart_log_path),
    ]
    if use_reset:
        cmd.append("--reset")

    print("[diag] Starting UART collection: %s" % " ".join(cmd))

    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except Exception as exc:
        print("[diag] WARNING: subprocess failed to start: %s" % exc)
        return uart_log_path, False

    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)

    if proc.returncode != 0:
        print(
            "[diag] WARNING: collect_uart_log exited with code %d -- continuing"
            % proc.returncode
        )
        return uart_log_path, False

    return uart_log_path, True


# ---------------------------------------------------------------------------
# REQ-03: Log analysis
# ---------------------------------------------------------------------------

def _run_analysis(uart_log_path, output_dir, ts):
    """
    Import analyze_uart_log, run analyze_log() on the UART file, write the
    Markdown report.

    Returns (report_path, analysis_result_dict, error_str).
    """
    report_path = output_dir / ("issue_report_%s.md" % ts)

    if not uart_log_path.is_file():
        msg = "UART log not found: %s" % uart_log_path
        print("[diag] WARNING: %s -- skipping analysis" % msg)
        return report_path, None, msg

    try:
        ana_mod = _load_module("analyze_uart_log", _TOOLS_DIR / "analyze_uart_log.py")
    except Exception as exc:
        msg = "failed to import analyze_uart_log: %s" % exc
        print("[diag] WARNING: %s" % msg)
        return report_path, None, msg

    try:
        with open(str(uart_log_path), "r", encoding="utf-8", errors="replace") as fh:
            raw_lines = [l.rstrip("\r\n") for l in fh.readlines()]
    except Exception as exc:
        msg = "could not read UART log: %s" % exc
        print("[diag] WARNING: %s" % msg)
        return report_path, None, msg

    try:
        analysis = ana_mod.analyze_log(raw_lines)
    except Exception as exc:
        msg = "analyze_log raised: %s" % exc
        print("[diag] WARNING: %s" % msg)
        return report_path, None, msg

    # Render and write report using the module's internal renderer
    try:
        generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        source_name = uart_log_path.name
        report_text = ana_mod.render_report(analysis, source_name, generated_at)
        report_path.write_text(report_text, encoding="utf-8")
        print("[diag] Issue report -> %s" % report_path)
    except Exception as exc:
        print("[diag] WARNING: could not write report: %s" % exc)
        return report_path, analysis, str(exc)

    return report_path, analysis, ""


# ---------------------------------------------------------------------------
# REQ-04: Final summary
# ---------------------------------------------------------------------------

def _print_summary(output_files, analysis):
    print("")
    print("=" * 50)
    print("Diagnostic run complete")
    print("=" * 50)
    print("Output files:")
    for label, path in output_files:
        exists = path.is_file() if path else False
        tag = "(ok)" if exists else "(missing)"
        print("  %-35s %s" % (label, tag))
        if exists:
            print("    -> %s" % path)

    print("")
    if analysis is None:
        print("Health: UNKNOWN (analysis not available)")
        return

    n_crit = len(analysis.get("criticals") or [])
    n_warn = len(analysis.get("warnings") or [])
    if n_crit == 0 and n_warn == 0:
        print("Health: CLEAN")
    else:
        print("Health: ISSUES FOUND (%d critical, %d warning)" % (n_crit, n_warn))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", default="COM13", help="Serial port (default: COM13)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="UART collection duration in seconds (default: 300)",
    )
    parser.add_argument(
        "--output-dir",
        default="tools/logs",
        help="Directory for output files (default: tools/logs)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Pass --reset flag through to collect_uart_log.py",
    )
    args = parser.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print("[diag] ERROR: could not create output dir %s: %s" % (output_dir, exc))
        sys.exit(1)

    print("[diag] === ESP32 Diagnostic Run  ts=%s ===" % ts)
    print("[diag] Port: %s  Baud: %d  Duration: %ds" % (args.port, args.baud, args.duration))
    print("[diag] Output dir: %s" % output_dir.resolve())
    print("")

    # REQ-01: Display capture
    print("[diag] --- Step 1: Display capture ---")
    status_path, html_path, _sweep_result, _disp_err = _run_display_capture(
        args.port, args.baud, output_dir, ts
    )

    # REQ-02: UART collection
    print("[diag] --- Step 2: UART collection (%ds) ---" % args.duration)
    uart_log_path, _uart_ok = _run_uart_collection(
        args.port, args.baud, args.duration, output_dir, ts, args.reset
    )

    # REQ-03: Analysis
    print("[diag] --- Step 3: Log analysis ---")
    report_path, analysis, _ana_err = _run_analysis(uart_log_path, output_dir, ts)

    # REQ-04: Summary
    output_files = [
        ("display_status_%s.log" % ts, status_path),
        ("display_snapshot_%s.html" % ts, html_path),
        ("uart_raw_%s.log" % ts, uart_log_path),
        ("issue_report_%s.md" % ts, report_path),
    ]
    _print_summary(output_files, analysis)


if __name__ == "__main__":
    main()
