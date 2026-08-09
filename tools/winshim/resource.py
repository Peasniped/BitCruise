"""Windows stand-in for the Unix-only ``resource`` module.

Home Assistant uses it to raise the open file descriptor limit at startup, which
tests do not depend on. See ``fcntl.py`` in this directory for why these exist.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":  # pragma: no cover - guard only
    raise ImportError(
        "tools/winshim/resource.py is a Windows-only stub and must never shadow "
        "the real resource module"
    )

RLIMIT_CORE = 4
RLIMIT_NOFILE = 7
RLIM_INFINITY = -1


def getrlimit(which: int) -> tuple[int, int]:
    """Report a generous, fixed limit."""
    return (8192, 8192)


def setrlimit(which: int, limits: tuple[int, int]) -> None:
    """Accept the change without doing anything."""
    return None
