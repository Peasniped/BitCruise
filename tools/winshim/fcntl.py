"""Windows stand-in for the Unix-only ``fcntl`` module.

Home Assistant imports ``fcntl`` in ``homeassistant.runner`` solely to take an
exclusive lock on its single-instance file. Tests never execute that path, so
stubbing it is what lets the Home Assistant test suite import on Windows at all.

Only reachable when this directory is on ``PYTHONPATH``, which the documented
Windows test command does and nothing else does. It refuses to import anywhere
else, so it can never shadow the real module on Linux or macOS.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":  # pragma: no cover - guard only
    raise ImportError(
        "tools/winshim/fcntl.py is a Windows-only stub and must never shadow "
        "the real fcntl module"
    )

LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8

F_GETFL = 3
F_SETFL = 4


def flock(fd: object, operation: int) -> None:
    """Pretend the lock was taken."""
    return None


def lockf(
    fd: object, cmd: int, length: int = 0, start: int = 0, whence: int = 0
) -> None:
    """Pretend the lock was taken."""
    return None


def fcntl(fd: object, cmd: int, arg: int = 0) -> int:
    """Return a benign flag set."""
    return 0


def ioctl(fd: object, request: int, arg: int = 0, mutate_flag: bool = True) -> int:
    """Return a benign result."""
    return 0
