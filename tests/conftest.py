"""Shared fixtures for the BitCruise test suite.

This module must not import Home Assistant at collection time. Home Assistant
cannot be imported on Windows (``homeassistant.runner`` imports the Unix-only
``fcntl`` module), and the pure planner tests are required to run without it.
Home Assistant fixtures live in ``tests/ha/conftest.py`` instead.
"""
