"""
analyze_uart_log.py — Parse a raw UART log from an ESP32 MicroPython device and
produce a Markdown issue report.

Usage:
    python tools/analyze_uart_log.py <log_file> [--output <report.md>]

Options:
    log_file    Path to the uart_raw_*.log or any raw UART capture file.
    --output    Path for the Markdown report. Defaults to
                tools/logs/issue_report_YYYYMMDD_HHMMSS.md relative to the
                working directory.

The script can also be imported and used programmatically:
    from tools.analyze_uart_log import analyze_log
    result = analyze_log(lines)

analyze_log(lines) returns a dict with keys:
    total_lines, error_count, warning_count, tracebacks, criticals, warnings,
    informationals, mem_heap, esp_ram, tags_seen, first_ts, last_ts,
    repetitions.
"""

import re
import os
import sys
import argparse
import datetime

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

TS_RE = re.compile(r'^\[(\d{2}:\d{2}:\d{2}\.\d{3})\] (.*)')
TAG_RE = re.compile(r'^\[([A-Z][A-Z0-9]+)\]')
MEM_RE = re.compile(r'\[MEM\] Free heap:\s*(\d+)\s*bytes', re.IGNORECASE)
RAM_RE = re.compile(r'\[ESP\] RAM\s+(\d+)kB free', re.IGNORECASE)

CRITICAL_PATTERNS = [
    ('traceback',      re.compile(r'Traceback \(most recent call last\)', re.IGNORECASE)),
    ('MemoryError',    re.compile(r'MemoryError', re.IGNORECASE)),
    ('fatal',          re.compile(r'\bfatal\b', re.IGNORECASE)),
    ('wdt_reset',      re.compile(r'WDT reset|watchdog', re.IGNORECASE)),
    ('guru_meditation',re.compile(r'guru meditation', re.IGNORECASE)),
    ('rst_cause',      re.compile(r'rst cause:', re.IGNORECASE)),
    ('AssertionError', re.compile(r'AssertionError', re.IGNORECASE)),
]

WARNING_PATTERNS = [
    ('oserror_net',    re.compile(r'OSError:\s*-20[23]?(?:\b|$)')),
    ('fetch_error',    re.compile(r'fetch error:', re.IGNORECASE)),
    ('bind_error',     re.compile(r'bind/listen error:', re.IGNORECASE)),
    ('pre_bind_failed',re.compile(r'Pre-bind failed:', re.IGNORECASE)),
    ('backoff',        re.compile(r'\bbackoff\b', re.IGNORECASE)),
    ('stale',          re.compile(r'\[stale\]', re.IGNORECASE)),
    ('errno',          re.compile(r'\[Errno\s+\d+\]')),
]

# ---------------------------------------------------------------------------
# Root cause hypotheses keyed by pattern name
# ---------------------------------------------------------------------------

ROOT_CAUSES = {
    'oserror_net':     {
        '-202': 'No route to host — firewall blocking port or server not running',
        '-203': 'Connection refused — target service not listening on that port',
        '-2':   'No route to host (ENONET/EHOSTUNREACH)',
    },
    'fetch_error':     'API fetch failure — check connectivity and target service; '
                       '-202 = no route, -203 = connection refused',
    'bind_error':      'Socket already in use or ENOBUFS — web config server could not bind',
    'backoff':         'Repeated failure causing exponential backoff — check connectivity',
    'stale':           'Weather cache is stale — OWM fetch has been failing',
    'errno':           'System errno raised — see errno number for details',
    'pre_bind_failed': 'Socket setup issue — ENOBUFS at startup; will retry in task',
    'MemoryError':     'Heap exhausted — check for large allocations or memory leaks',
    'wdt_reset':       'Task blocked too long — async task doing blocking I/O or infinite loop',
    'guru_meditation': 'ESP32 core panic — hardware-level crash, likely stack overflow or corrupt memory',
    'traceback':       'Unhandled exception in async task — review traceback for root cause',
    'fatal':           'Fatal error logged — review context lines for cause',
    'rst_cause':       'ESP32 reset event — may follow a crash or WDT reset',
    'AssertionError':  'Assertion failed — unexpected state in application code',
}


def _strip_timestamp(line):
    """Return (timestamp_str_or_None, content) from a raw log line."""
    m = TS_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    return None, line


def _short_title(content, max_len=60):
    """Truncate content to a short title string."""
    content = content.strip()
    if len(content) > max_len:
        return content[:max_len - 3] + '...'
    return content


def _root_cause(pattern_name, content):
    """Return a root-cause hypothesis string for a given pattern + content."""
    hint = ROOT_CAUSES.get(pattern_name, '')
    if pattern_name == 'oserror_net' and isinstance(hint, dict):
        for code, msg in hint.items():
            if code in content:
                return msg
        return 'Network error — check connectivity'
    if isinstance(hint, dict):
        return str(hint)
    return hint or 'Unknown — review log context'


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_log(lines):
    """
    Parse a list of raw log lines and return a structured analysis dict.

    Parameters
    ----------
    lines : list[str]
        Raw lines from the UART log file (with or without timestamps).

    Returns
    -------
    dict with keys:
        total_lines, error_count, warning_count, tracebacks, criticals,
        warnings, informationals, mem_heap, esp_ram, tags_seen,
        first_ts, last_ts, repetitions.
    """
    criticals = []   # list of issue dicts
    warnings  = []
    boot_events = []

    # memory tracking
    heap_values = []
    ram_values  = []

    tags_seen   = set()
    first_ts    = None
    last_ts     = None

    error_count   = 0
    warning_count = 0

    # For deduplication: key -> issue dict already in list
    crit_dedup = {}
    warn_dedup = {}

    # For repetition detection
    prev_content  = None
    repeat_count  = 1
    repeat_start  = 0
    repetitions   = []

    # Traceback state
    in_traceback  = False
    tb_lines      = []
    tb_lineno     = 0
    tb_ts         = None

    def _flush_traceback():
        if not tb_lines:
            return
        raw = '\n'.join(tb_lines)
        key = raw
        if key in crit_dedup:
            crit_dedup[key]['occurrences'] += 1
        else:
            issue = {
                'id':         'traceback',
                'title':      _short_title(tb_lines[1] if len(tb_lines) > 1 else tb_lines[0]),
                'severity':   'Critical',
                'occurrences': 1,
                'first_ts':   tb_ts or '',
                'first_line': tb_lineno,
                'pattern':    'traceback',
                'context':    raw,
                'hypothesis': ROOT_CAUSES['traceback'],
            }
            criticals.append(issue)
            crit_dedup[key] = issue

    def _maybe_flush_repeat(idx):
        if repeat_count > 5 and prev_content is not None:
            repetitions.append({
                'content':    prev_content,
                'count':      repeat_count,
                'start_line': repeat_start,
                'end_line':   idx - 1,
            })

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip('\r\n')
        if not line:
            # blank line may end a traceback
            if in_traceback:
                _flush_traceback()
                in_traceback = False
                tb_lines = []
            if prev_content is not None:
                _maybe_flush_repeat(lineno)
                prev_content = None
                repeat_count = 1
            continue

        ts, content = _strip_timestamp(line)

        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

        # --- repetition tracking ---
        if content == prev_content:
            repeat_count += 1
        else:
            if prev_content is not None:
                _maybe_flush_repeat(lineno)
            prev_content  = content
            repeat_count  = 1
            repeat_start  = lineno

        # --- tag collection ---
        tm = TAG_RE.match(content)
        if tm:
            tags_seen.add(tm.group(1))

        # --- traceback continuation ---
        if in_traceback:
            # end traceback on non-indented, non-blank line that looks like a new tag
            if TAG_RE.match(content) and not content.startswith(' '):
                _flush_traceback()
                in_traceback = False
                tb_lines = []
                # fall through to process this line normally
            else:
                tb_lines.append(content)
                continue

        # --- memory snapshots ---
        mm = MEM_RE.search(content)
        if mm:
            heap_values.append(int(mm.group(1)))

        rm = RAM_RE.search(content)
        if rm:
            ram_values.append(int(rm.group(1)))

        # --- boot events ---
        if content.startswith('[BOOT]'):
            boot_events.append({'lineno': lineno, 'ts': ts or '', 'content': content})

        # --- critical pattern matching ---
        matched_critical = False
        for pname, pat in CRITICAL_PATTERNS:
            if not pat.search(content):
                continue
            matched_critical = True
            if pname == 'traceback':
                in_traceback = True
                tb_lines     = [content]
                tb_lineno    = lineno
                tb_ts        = ts or ''
                error_count += 1
                break
            key = '%s|%s' % (pname, content)
            if key in crit_dedup:
                crit_dedup[key]['occurrences'] += 1
            else:
                issue = {
                    'id':         pname,
                    'title':      _short_title(content),
                    'severity':   'Critical',
                    'occurrences': 1,
                    'first_ts':   ts or '',
                    'first_line': lineno,
                    'pattern':    pname,
                    'context':    content,
                    'hypothesis': _root_cause(pname, content),
                }
                criticals.append(issue)
                crit_dedup[key] = issue
            error_count += 1
            break

        if matched_critical:
            continue

        # --- warning pattern matching ---
        for pname, pat in WARNING_PATTERNS:
            if not pat.search(content):
                continue
            key = '%s|%s' % (pname, content)
            if key in warn_dedup:
                warn_dedup[key]['occurrences'] += 1
            else:
                issue = {
                    'id':         pname,
                    'title':      _short_title(content),
                    'severity':   'Warning',
                    'occurrences': 1,
                    'first_ts':   ts or '',
                    'first_line': lineno,
                    'pattern':    pname,
                    'context':    content,
                    'hypothesis': _root_cause(pname, content),
                }
                warnings.append(issue)
                warn_dedup[key] = issue
            warning_count += 1
            break

    # flush trailing traceback
    if in_traceback:
        _flush_traceback()

    # flush trailing repetition
    if prev_content is not None and repeat_count > 5:
        repetitions.append({
            'content':    prev_content,
            'count':      repeat_count,
            'start_line': repeat_start,
            'end_line':   len(lines),
        })

    # --- informational: boot events + memory + repetitions ---
    informationals = []

    if boot_events:
        informationals.append({
            'id':    'boot_sequence',
            'title': 'Device boot sequence (%d events)' % len(boot_events),
            'items': boot_events,
        })

    if heap_values:
        informationals.append({
            'id':    'mem_heap',
            'title': 'Heap snapshots',
            'min':   min(heap_values),
            'max':   max(heap_values),
            'last':  heap_values[-1],
            'count': len(heap_values),
        })

    if ram_values:
        informationals.append({
            'id':    'esp_ram',
            'title': 'ESP RAM free snapshots',
            'min':   min(ram_values),
            'max':   max(ram_values),
            'count': len(ram_values),
        })

    if repetitions:
        for rep in repetitions:
            informationals.append({
                'id':    'repetition',
                'title': 'Repeated line (%dx): %s' % (rep['count'], _short_title(rep['content'], 40)),
                'rep':   rep,
            })

    return {
        'total_lines':   len(lines),
        'error_count':   error_count,
        'warning_count': warning_count,
        'tracebacks':    sum(1 for c in criticals if c['id'] == 'traceback'),
        'wdt_resets':    sum(c['occurrences'] for c in criticals if c['id'] == 'wdt_reset'),
        'criticals':     criticals,
        'warnings':      warnings,
        'informationals':informationals,
        'mem_heap':      {'min': min(heap_values), 'max': max(heap_values), 'last': heap_values[-1]}
                         if heap_values else None,
        'esp_ram':       {'min': min(ram_values), 'max': max(ram_values)}
                         if ram_values else None,
        'tags_seen':     sorted(tags_seen),
        'first_ts':      first_ts or '',
        'last_ts':       last_ts or '',
        'repetitions':   repetitions,
    }


# ---------------------------------------------------------------------------
# Markdown report rendering
# ---------------------------------------------------------------------------

def _render_report(result, source_name, generated_at):
    """Return the full Markdown report as a string."""
    r = result
    lines = []

    lines.append('# Issue Report — %s' % source_name)
    lines.append('Generated: %s' % generated_at)
    lines.append('Log lines: %d | Errors: %d | Warnings: %d' % (
        r['total_lines'], r['error_count'], r['warning_count']))
    lines.append('')

    # --- Summary ---
    lines.append('## Summary')
    h = r['mem_heap']
    if h:
        lines.append('- Memory: heap min %d B, max %d B, last %d B' % (h['min'], h['max'], h['last']))
    else:
        lines.append('- Memory: no heap snapshots found')
    rr = r['esp_ram']
    if rr:
        lines.append('- RAM free: min %d kB, max %d kB' % (rr['min'], rr['max']))
    else:
        lines.append('- RAM free: no ESP RAM lines found')
    lines.append('- Tracebacks: %d' % r['tracebacks'])
    lines.append('- Watchdog resets: %d' % r['wdt_resets'])
    lines.append('- Unique errors: %d' % len(r['criticals']))
    lines.append('- Duration estimate: %s to %s' % (r['first_ts'] or 'n/a', r['last_ts'] or 'n/a'))
    lines.append('- Tags seen: %s' % (', '.join('[%s]' % t for t in r['tags_seen']) if r['tags_seen'] else 'none'))
    lines.append('')

    # --- Critical Issues ---
    lines.append('## Critical Issues')
    if not r['criticals']:
        lines.append('_None detected._')
    else:
        for idx, issue in enumerate(r['criticals'], start=1):
            lines.append('### [C%d] %s' % (idx, issue['title']))
            lines.append('- **Severity:** %s' % issue['severity'])
            lines.append('- **Occurrences:** %d' % issue['occurrences'])
            ts_str = issue['first_ts'] if issue['first_ts'] else 'n/a'
            lines.append('- **First seen:** %s (line %d)' % (ts_str, issue['first_line']))
            lines.append('- **Pattern:** `%s`' % issue['pattern'])
            lines.append('- **Context:**')
            lines.append('  ```')
            for ctx_line in issue['context'].splitlines():
                lines.append('  %s' % ctx_line)
            lines.append('  ```')
            lines.append('- **Root cause hypothesis:** %s' % issue['hypothesis'])
            lines.append('')

    lines.append('')

    # --- Warnings ---
    lines.append('## Warnings')
    if not r['warnings']:
        lines.append('_None detected._')
    else:
        for idx, issue in enumerate(r['warnings'], start=1):
            lines.append('### [W%d] %s' % (idx, issue['title']))
            lines.append('- **Severity:** %s' % issue['severity'])
            lines.append('- **Occurrences:** %d' % issue['occurrences'])
            ts_str = issue['first_ts'] if issue['first_ts'] else 'n/a'
            lines.append('- **First seen:** %s (line %d)' % (ts_str, issue['first_line']))
            lines.append('- **Pattern:** `%s`' % issue['pattern'])
            lines.append('- **Context:**')
            lines.append('  ```')
            for ctx_line in issue['context'].splitlines():
                lines.append('  %s' % ctx_line)
            lines.append('  ```')
            lines.append('- **Root cause hypothesis:** %s' % issue['hypothesis'])
            lines.append('')

    lines.append('')

    # --- Informational ---
    lines.append('## Informational')
    if not r['informationals']:
        lines.append('_None._')
    else:
        info_idx = 1
        for info in r['informationals']:
            if info['id'] == 'boot_sequence':
                lines.append('### [I%d] %s' % (info_idx, info['title']))
                for ev in info['items']:
                    ts_str = ev['ts'] if ev['ts'] else 'n/a'
                    lines.append('- Line %d (%s): `%s`' % (ev['lineno'], ts_str, ev['content']))
                lines.append('')
                info_idx += 1
            elif info['id'] == 'mem_heap':
                lines.append('### [I%d] Heap snapshots (%d readings)' % (info_idx, info['count']))
                lines.append('- Min: %d B, Max: %d B, Last: %d B' % (info['min'], info['max'], info['last']))
                lines.append('')
                info_idx += 1
            elif info['id'] == 'esp_ram':
                lines.append('### [I%d] ESP RAM free snapshots (%d readings)' % (info_idx, info['count']))
                lines.append('- Min: %d kB, Max: %d kB' % (info['min'], info['max']))
                lines.append('')
                info_idx += 1
            elif info['id'] == 'repetition':
                rep = info['rep']
                lines.append('### [I%d] %s' % (info_idx, info['title']))
                lines.append('- Lines %d–%d: `%s` repeated %d times' % (
                    rep['start_line'], rep['end_line'], _short_title(rep['content'], 50), rep['count']))
                lines.append('')
                info_idx += 1

    lines.append('')

    # --- Raw Statistics ---
    lines.append('## Raw Statistics')
    lines.append('- Lines captured: %d' % r['total_lines'])
    lines.append('- Duration estimate: %s to %s' % (r['first_ts'] or 'n/a', r['last_ts'] or 'n/a'))
    lines.append('- Tags seen: %s' % (', '.join('[%s]' % t for t in r['tags_seen']) if r['tags_seen'] else 'none'))
    lines.append('- Critical issues (unique): %d' % len(r['criticals']))
    lines.append('- Warning issues (unique): %d' % len(r['warnings']))
    lines.append('- Total error lines: %d' % r['error_count'])
    lines.append('- Total warning lines: %d' % r['warning_count'])
    if r['repetitions']:
        lines.append('- Repeated-line events (>5 in a row): %d' % len(r['repetitions']))

    lines.append('')
    return '\n'.join(lines)


render_report = _render_report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Analyze a UART log from an ESP32 MicroPython device and produce a Markdown report.'
    )
    parser.add_argument('log_file', help='Path to the uart_raw_*.log or any raw UART capture file.')
    parser.add_argument(
        '--output', default=None,
        help='Output path for the Markdown report. Defaults to tools/logs/issue_report_YYYYMMDD_HHMMSS.md'
    )
    args = parser.parse_args()

    log_path = args.log_file
    if not os.path.isfile(log_path):
        sys.stderr.write('Error: log file not found: %s\n' % log_path)
        sys.exit(1)

    with open(log_path, 'r', encoding='utf-8', errors='replace') as fh:
        raw_lines = fh.readlines()

    generated_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ts_slug      = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

    if args.output:
        out_path = args.output
    else:
        out_dir  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, 'issue_report_%s.md' % ts_slug)

    result = analyze_log([l.rstrip('\r\n') for l in raw_lines])

    source_name = os.path.basename(log_path)
    report = _render_report(result, source_name, generated_at)

    out_dir_parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir_parent, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(report)

    # stdout summary
    r = result
    print('Analyzed: %s' % log_path)
    print('Lines: %d | Errors: %d | Warnings: %d' % (r['total_lines'], r['error_count'], r['warning_count']))
    h = r.get('mem_heap')
    if h:
        print('Heap: min=%d B  max=%d B  last=%d B' % (h.get('min', 0), h.get('max', 0), h.get('last', 0)))
    rr = r.get('esp_ram')
    if rr:
        print('RAM free: min=%d kB  max=%d kB' % (rr.get('min', 0), rr.get('max', 0)))
    print('Tracebacks: %d  WDT resets: %d' % (r['tracebacks'], r['wdt_resets']))
    print('Tags: %s' % ', '.join('[%s]' % t for t in r['tags_seen']))
    print('Report saved to: %s' % out_path)


if __name__ == '__main__':
    main()
