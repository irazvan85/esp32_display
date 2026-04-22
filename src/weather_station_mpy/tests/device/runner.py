"""Minimal test runner for MicroPython HIL tests.

This file is NOT imported — the HIL runner on the host concatenates it with
each test file into a single temporary script and runs it via:

    mpremote connect <port> run <combined_temp.py>

All helpers defined here are therefore available in the test file's global
scope without any import statement.

Output protocol (parsed by hil_runner.py):
    [TEST] <name> PASS
    [TEST] <name> FAIL: <reason>
    [TEST] SUMMARY <n_pass>/<n_total> PASS|FAIL
"""

import gc as _gc

_pass_count = 0
_fail_count = 0


def run(name, fn):
    """Execute fn(); record PASS or FAIL with structured UART output."""
    global _pass_count, _fail_count
    _gc.collect()
    try:
        fn()
        print("[TEST] %s PASS" % name)
        _pass_count += 1
    except Exception as _e:
        print("[TEST] %s FAIL: %s" % (name, str(_e)))
        _fail_count += 1


def assert_true(cond, msg="expected True"):
    if not cond:
        raise AssertionError(msg)


def assert_false(cond, msg="expected False"):
    if cond:
        raise AssertionError(msg)


def assert_equal(a, b, msg=None):
    if a != b:
        raise AssertionError(msg or ("%r != %r" % (a, b)))


def assert_not_equal(a, b, msg=None):
    if a == b:
        raise AssertionError(msg or ("expected %r != %r" % (a, b)))


def assert_gt(a, b, msg=None):
    if not (a > b):
        raise AssertionError(msg or ("%r not > %r" % (a, b)))


def assert_ge(a, b, msg=None):
    if not (a >= b):
        raise AssertionError(msg or ("%r not >= %r" % (a, b)))


def assert_lt(a, b, msg=None):
    if not (a < b):
        raise AssertionError(msg or ("%r not < %r" % (a, b)))


def assert_is_none(val, msg=None):
    if val is not None:
        raise AssertionError(msg or ("expected None, got %r" % val))


def assert_is_not_none(val, msg="expected non-None"):
    if val is None:
        raise AssertionError(msg)


def assert_in(item, container, msg=None):
    if item not in container:
        raise AssertionError(msg or ("%r not in %r" % (item, container)))


def summary():
    """Print final tally; return True on all-pass."""
    total = _pass_count + _fail_count
    status = "PASS" if _fail_count == 0 else "FAIL"
    print("[TEST] SUMMARY %d/%d %s" % (_pass_count, total, status))
    return _fail_count == 0
