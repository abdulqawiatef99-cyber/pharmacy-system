"""#85: after an install or update the browser must load the new app.js on a normal reload.
Found on the pharmacy PC: /static had no Cache-Control and index.html used a bare /static/app.js,
so the page kept running the old module against the new server."""
import os
import re
import shutil
from unittest import mock

from starlette.testclient import TestClient

from app.api import app as api
from tests.test_api import APICase


class TestStaticCache(APICase):
    def setUp(self):
        super().setUp()
        self.web = self.tmp / "web"
        shutil.copytree(api.WEB_DIR, self.web)
        patch = mock.patch.object(api, "WEB_DIR", self.web)
        patch.start()
        self.addCleanup(patch.stop)
        self.c = TestClient(api.create_app(self.db, self.s))

    def shell_urls(self):
        page = self.c.get("/")
        self.assertEqual(page.headers["cache-control"], "no-cache")
        return dict(re.findall(r'"/static/([\w.-]+)\?v=([0-9a-f]{10})"', page.text))

    def test_every_shell_asset_is_versioned_and_revalidated(self):
        urls = self.shell_urls()
        self.assertEqual(set(urls), set(api.SHELL_ASSETS))
        self.assertNotIn('"/static/app.js"', self.c.get("/").text)
        for name, v in urls.items():
            r = self.c.get(f"/static/{name}?v={v}")
            self.assertEqual(r.status_code, 200, name)
            self.assertEqual(r.headers["cache-control"], "no-cache", name)
        self.assertEqual(self.c.get("/static/print.js").headers["cache-control"], "no-cache")
        self.assertEqual(self.c.get("/api/health").headers["cache-control"], "no-store")

    def test_a_page_loaded_before_the_update_gets_the_new_js_on_reload(self):
        before = self.shell_urls()
        js = self.web / "app.js"
        js.write_text(js.read_text(encoding="utf-8") + "\n// updated\n", encoding="utf-8")
        st = js.stat()
        os.utime(js, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))   # same second on coarse clocks
        after = self.shell_urls()                                             # no server restart
        self.assertNotEqual(before["app.js"], after["app.js"])
        self.assertEqual(before["app.css"], after["app.css"])
        self.assertIn("// updated", self.c.get(f"/static/app.js?v={after['app.js']}").text)

    def test_language_file_and_service_worker_follow_the_version(self):
        src = (self.web / "app.js").read_text(encoding="utf-8")
        self.assertIn('meta[name="i18n-src"]', src)
        sw = (self.web / "sw.js").read_text(encoding="utf-8")
        self.assertIn("ignoreSearch: true", sw)
        self.assertIn("c.put(url.pathname, copy)", sw)
