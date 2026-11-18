"""The server log keeps the path of each request, never its query string (#140).

uvicorn's access log wrote full URLs to ``logs/server.log``; a customer search puts the phone number
or the name in the query (``/api/customers?q=010...``). The filter cuts everything after "?".
"""
from __future__ import annotations

import copy
import logging


class PathOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def uvicorn_log_config() -> dict:
    from uvicorn.config import LOGGING_CONFIG
    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg.setdefault("filters", {})["path_only"] = {"()": PathOnly}
    cfg["handlers"]["access"].setdefault("filters", []).append("path_only")
    return cfg
