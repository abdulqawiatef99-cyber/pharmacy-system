"""The latest time the program has seen on this PC ("high-water mark"), against turning the clock back.

A subscription ends on a date, so the cheapest attack is to set the PC clock to last year. The answer:
remember the latest time ever seen and never judge a licence by an earlier one (license.evaluate).

The mark is kept in more than one small file (the caller chooses the places, e.g. the data folder and
a second folder outside it); the latest readable value wins, so deleting one copy changes nothing.
The pharmacy's own records are a third witness the caller can add (`evidence`): a sale dated next
March proves it is at least next March, and those records cannot be deleted without losing the data.

Limits, stated plainly: someone who deletes every copy AND works on an empty database starts fresh;
a clock set far into the future by mistake raises the mark for good (the publisher then issues a
licence that covers the wrong date; a support case, not a lock-out of the data).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .license import LicenseError, format_time, parse_time

FILE_NAME = "license-clock.json"


def read(paths) -> datetime | None:
    """Latest mark among the copies; None when no copy is readable. A damaged copy is skipped."""
    best = None
    for p in paths:
        try:
            value = json.loads(Path(p).read_text(encoding="utf-8"))
            seen = parse_time(value["seen"]) if isinstance(value, dict) else None
        except (OSError, ValueError, KeyError, TypeError, LicenseError):
            continue
        if seen is not None and (best is None or seen > best):
            best = seen
    return best


def high_water(paths, *evidence: datetime | None) -> datetime | None:
    """The mark to judge by: the latest of the stored copies and any dated evidence from the data."""
    times = [t.astimezone(timezone.utc) for t in evidence if t is not None]
    stored = read(paths)
    if stored is not None:
        times.append(stored)
    return max(times) if times else None


def remember(paths, now: datetime, *evidence: datetime | None) -> datetime:
    """Raise the mark to the latest of `now`, the stored copies and the evidence, in every copy.
    Never lowers it. A copy that cannot be written is skipped (a read-only folder must not stop the
    program); returns the mark."""
    if now.tzinfo is None:
        raise ValueError("pass an aware datetime")
    mark = max([now.astimezone(timezone.utc)] + [t for t in [high_water(paths, *evidence)] if t is not None])
    text = json.dumps({"seen": format_time(mark)})
    for p in paths:
        p = Path(p)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".clock-", dir=p.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, p)
            finally:
                Path(tmp).unlink(missing_ok=True)
        except OSError:
            continue
    return mark
