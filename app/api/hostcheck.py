"""Host header allowlist against DNS rebinding (#136).

A web page opened on the pharmacy PC can re-point its own domain to 127.0.0.1; its script then talks
to the program with ``Host: evil.example:8000`` while the TCP peer is 127.0.0.1, which the "this PC
only" routes trust. Browsers always send the name the page was loaded from, so refusing every Host
the program does not serve closes that path.

Accepted:
- the loopback names ``127.0.0.1``, ``localhost`` and ``[::1]``;
- the exact address the connection arrived on (uvicorn's ``scope["server"]``): in LAN mode a phone
  opens ``http://192.168.1.20:8000/`` and sends that address, which an attacker's page cannot;
- any name listed in ``PHARM_ALLOWED_HOSTS`` (comma separated), for an explicitly configured LAN name.
When the Host carries a port, it must be the port the program listens on.
"""
from __future__ import annotations

import json

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
MESSAGE = "عنوان البرنامج غير معروف: افتح البرنامج من أيقونته على الجهاز (127.0.0.1)"


def split_host(value: str) -> tuple[str, int | None] | None:
    """``name[:port]`` / ``[v6][:port]`` → (lower-case name, port or None); None when malformed."""
    value = value.strip().lower()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        name, rest = value[1:end], value[end + 1:]
        if rest and not rest.startswith(":"):
            return None
        port_s = rest[1:] if rest else ""
    elif value.count(":") == 1:
        name, port_s = value.split(":")
    elif ":" in value:
        return None                      # bare IPv6 without brackets is not a valid Host
    else:
        name, port_s = value, ""
    name = name.rstrip(".")
    if not name:
        return None
    if port_s:
        if not port_s.isdigit() or not 0 < int(port_s) < 65536:
            return None
        return name, int(port_s)
    return name, None


class HostAllowlist:
    """Pure ASGI middleware: answers 403 ``bad_host`` before any route runs."""

    def __init__(self, app, extra_hosts: tuple[str, ...] = ()):
        self.app = app
        self.extra = frozenset(h.strip().lower().rstrip(".") for h in extra_hosts if h.strip())

    def allowed(self, scope) -> bool:
        host = None
        for k, v in scope.get("headers") or ():
            if k == b"host":
                host = v.decode("latin-1")
                break
        if host is None:
            return False
        parsed = split_host(host)
        if not parsed:
            return False
        name, port = parsed
        server = scope.get("server") or (None, None)
        server_host = str(server[0]).lower() if server[0] else None
        server_port = server[1] if len(server) > 1 else None
        if port is not None and server_port is not None and port != server_port:
            return False
        return name in LOOPBACK or name == server_host or name in self.extra

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not self.allowed(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = json.dumps({"error": {"code": "bad_host", "message": MESSAGE}}, ensure_ascii=False).encode("utf-8")
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                    (b"content-length", str(len(body)).encode()),
                                    (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
