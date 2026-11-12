"""A simulated clock for the load generator (synthetic data only, never the pharmacy's database).

The services take "now" from Python (`datetime.now()`, `date.today()`) and from SQLite column
defaults (`strftime('%Y-%m-%dT%H:%M:%fZ','now')`). To spread years of synthetic activity over
their real dates, both are redirected to one clock:

* Python: the `datetime` module's `datetime` / `date` names, and the same names in every loaded
  `app.*` module, become subclasses whose `now()` / `today()` read the clock (all other behaviour is
  the standard library's). This is process-wide, which is why only the generator installs it.
* SQLite: on the generator's own connections `strftime`, `datetime`, `date` and `julianday` are
  overridden by application functions that replace the 'now' argument with the clock and then
  call the built-in through a private in-memory connection.

Nothing here is imported by the application; it exists for scripts/perf only.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date as _date, datetime as _datetime, timedelta, timezone


class SimClock:
    def __init__(self, start_utc: _datetime):
        self.now_utc = start_utc.astimezone(timezone.utc)

    def set_local(self, day: _date, hour: int, minute: int = 0, second: int = 0) -> None:
        local = _datetime(day.year, day.month, day.day, hour, minute, second).astimezone()
        self.now_utc = local.astimezone(timezone.utc)

    def advance(self, seconds: float) -> None:
        self.now_utc += timedelta(seconds=seconds)

    def sql_now(self) -> str:
        return self.now_utc.strftime("%Y-%m-%d %H:%M:%S.") + f"{self.now_utc.microsecond // 1000:03d}"


def import_app() -> None:
    """Load every app module first, so each one's `datetime` / `date` can be redirected."""
    import importlib
    import pkgutil
    import app
    for m in pkgutil.walk_packages(app.__path__, "app."):
        importlib.import_module(m.name)


def install_python(clock: SimClock) -> None:
    import_app()
    class SimDateTime(_datetime):
        @classmethod
        def now(cls, tz=None):
            t = clock.now_utc
            return t.astimezone(tz) if tz else t.astimezone().replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return clock.now_utc.replace(tzinfo=None)

    class SimDate(_date):
        @classmethod
        def today(cls):
            return clock.now_utc.astimezone().date()

    import datetime as dtmod
    dtmod.datetime, dtmod.date = SimDateTime, SimDate        # function-local `from datetime import datetime`
    for name, mod in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")) or mod is None:
            continue
        if getattr(mod, "datetime", None) is _datetime:
            mod.datetime = SimDateTime
        if getattr(mod, "date", None) is _date:
            mod.date = SimDate


_SQL_TIME_FUNCS = ("strftime", "datetime", "date", "julianday", "time")


def install_sql(conn: sqlite3.Connection, clock: SimClock) -> None:
    side = sqlite3.connect(":memory:")

    def make(fn: str):
        def call(*args):
            if not args and fn != "strftime":
                args = ("now",)
            args = tuple(clock.sql_now() if isinstance(a, str) and a.lower() == "now" else a for a in args)
            return side.execute(f"SELECT {fn}({','.join('?' * len(args))})", args).fetchone()[0]
        return call

    for fn in _SQL_TIME_FUNCS:
        for n in range(0, 7):
            if fn == "strftime" and n == 0:
                continue
            conn.create_function(fn, n, make(fn))
