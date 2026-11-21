"""Keeping the cashier's requests quick while a background thread does heavy work in Python.

Python runs one thread at a time and lets a busy thread keep going for 5 ms before a waiting one may take over.
A request handler gives the interpreter up for every database call and must wait to get it back, up to those
5 ms each time, whenever another thread is busy in pure Python: the backup checking the activity log's hash
chain (180,000 rows a year in a medium pharmacy), or a year-long report adding rows up. A scan makes about a
hundred database calls, so it waited half a second (measured with scripts/perf: 576-742 ms during a backup,
107 ms with the interval below; F59, #304).

apply() shortens that turn to half a millisecond for the whole process. The busy thread loses a few percent
to the extra hand-overs; nobody waits for it any more.
"""
from __future__ import annotations

import sys

SWITCH_INTERVAL = 0.0005        # seconds


def apply() -> float:
    """Called once when the server's background jobs start. Never lengthens an interval already shorter."""
    if sys.getswitchinterval() > SWITCH_INTERVAL:
        sys.setswitchinterval(SWITCH_INTERVAL)
    return sys.getswitchinterval()
