"""Fixtures for the Home Assistant-dependent tests."""

import sys
from collections.abc import Generator

import pytest

if sys.platform == "win32":
    # The Home Assistant test harness blocks sockets to catch tests making real
    # network calls, allowing only AF_UNIX through for asyncio's own use. Windows
    # has no AF_UNIX, so asyncio's self-pipe trips the guard and no test can run.
    #
    # Neutralising it is confined to Windows on purpose: CI runs on Linux and
    # keeps the guard, so a test that genuinely reaches the network is still
    # caught before it can merge.
    import pytest_socket

    pytest_socket.disable_socket = lambda **_kwargs: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading custom integrations in every test in this package."""
    yield
