"""
Root conftest.py for the Scalper test suite.
Registers custom marks so pytest does not warn about unknown markers.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "mt5: mark test as requiring a live MetaTrader5 terminal (deselect with -m 'not mt5')"
    )
