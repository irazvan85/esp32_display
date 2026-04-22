"""Test runner — discovers and runs all tests in this directory.

Usage (from src/weather_station_mpy/):
    python tests/run_tests.py

Or via unittest discovery:
    python -m unittest discover -s tests -v
"""

import sys
import os
import unittest

# Ensure the app root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=os.path.dirname(__file__),
        pattern="test_*.py",
    )

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
