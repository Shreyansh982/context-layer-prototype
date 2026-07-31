"""Compatibility test runner; this file no longer mutates shared storage."""

import unittest


if __name__ == "__main__":
    unittest.main(module=None, argv=["python", "discover", "-s", "tests"])
