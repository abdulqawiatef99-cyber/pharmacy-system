"""#136: a foreign Host header (DNS rebinding) is refused before any route runs."""
from dataclasses import replace

from starlette.testclient import TestClient

from app.api.app import create_app
from app.api.hostcheck import split_host
from tests.test_api import APICase, H


class TestHostHeader(APICase):
    def _get(self, host, path="/api/auth/quick-users", client=None):
        return (client or self.c).get(path, headers={"Host": host})

    def test_rebinding_host_refused_on_local_only_routes(self):
        for path in ("/api/auth/quick-users", "/api/setup", "/", "/api/health"):
            r = self._get("rebind.attacker.example:8000", path)
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.json()["error"]["code"], "bad_host")
        r = self.c.post("/api/auth/pin", json={"username": "owner", "pin": "1234"},
                        headers={**H, "Host": "rebind.attacker.example"})
        self.assertEqual(r.status_code, 403)

    def test_malformed_or_missing_host_refused(self):
        for host in ("", "evil.example:abc", "::1", "[::1", "127.0.0.1:0", "127.0.0.1:99999"):
            self.assertEqual(self._get(host).status_code, 403, repr(host))

    def test_loopback_names_accepted(self):
        for host in ("127.0.0.1", "localhost", "LOCALHOST", "[::1]", "localhost."):
            self.assertEqual(self._get(host).status_code, 200, host)

    def test_port_must_match_listening_port(self):
        c = TestClient(self.app, base_url="http://127.0.0.1:8000")
        self.assertEqual(self._get("127.0.0.1:8000", client=c).status_code, 200)
        self.assertEqual(self._get("localhost:8000", client=c).status_code, 200)
        self.assertEqual(self._get("localhost:9000", client=c).status_code, 403)

    def test_lan_address_the_connection_arrived_on_accepted(self):
        # In LAN mode uvicorn reports the address the phone connected to as scope["server"].
        c = TestClient(self.app, base_url="http://192.168.1.20:8000")
        self.assertEqual(self._get("192.168.1.20:8000", client=c).status_code, 200)
        self.assertEqual(self._get("192.168.1.99:8000", client=c).status_code, 403)

    def test_owner_configured_host_accepted(self):
        app = create_app(self.db, replace(self.s, allowed_hosts=("Pharmacy-PC.local",)))
        c = TestClient(app)
        self.assertEqual(self._get("pharmacy-pc.local", client=c).status_code, 200)
        self.assertEqual(self._get("other.local", client=c).status_code, 403)

    def test_split_host(self):
        self.assertEqual(split_host("LocalHost:8000"), ("localhost", 8000))
        self.assertEqual(split_host("[::1]:8000"), ("::1", 8000))
        self.assertEqual(split_host("[::1]"), ("::1", None))
        self.assertIsNone(split_host("[::1]x"))
        self.assertIsNone(split_host("a:b:c"))
