"""Update button edges that had no test: where the GitHub key comes from, how GitHub answers are
read, and archive members the stager must refuse (trailing dot or space, symlink, reserved name,
size limits, unsafe root). Synthetic archives only; nothing reaches the network."""
import io
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from app.core import updater

BASE = {"manage.py": "x", "app/a.py": "x"}


def archive(files: dict[str, str], top="owner-repo-abc123", attrs: dict[str, int] | None = None) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            info = zipfile.ZipInfo(f"{top}/{name}")
            if attrs and name in attrs:
                info.external_attr = attrs[name] << 16
            z.writestr(info, text)
    return b.getvalue()


class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class Token(unittest.TestCase):
    def test_env_key_wins_and_is_stripped(self):
        with mock.patch.dict("os.environ", {"PHARM_GITHUB_TOKEN": "  synthetic-key\n"}):
            self.assertEqual(updater.read_token(), "synthetic-key")

    def test_key_file_is_read_and_stripped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "key.txt"
            p.write_text("file-key\r\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"PHARM_UPDATE_TOKEN_FILE": str(p)}) as env:
                env.pop("PHARM_GITHUB_TOKEN", None)
                self.assertEqual(updater.token_path(), p)
                self.assertEqual(updater.read_token(), "file-key")


class GitHubAnswers(unittest.TestCase):
    def test_request_carries_the_key_and_returns_the_body(self):
        seen = {}

        def fake(req, timeout):
            seen.update(url=req.full_url, auth=req.get_header("Authorization"), ua=req.get_header("User-agent"),
                        timeout=timeout)
            return _Resp(b"a" * 40 + b"\n")
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.assertEqual(updater.latest_sha("o/r", "develop", "k1"), "a" * 40)
        self.assertEqual(seen["url"], "https://api.github.com/repos/o/r/commits/develop")
        self.assertEqual((seen["auth"], seen["ua"], seen["timeout"]), ("Bearer k1", "pharmacy-updater", 120))

    def test_short_or_odd_sha_is_refused(self):
        for body in (b"", b"abc", b"a" * 41):
            with self.subTest(body=body), mock.patch("urllib.request.urlopen", return_value=_Resp(body)):
                with self.assertRaisesRegex(updater.UpdateError, "غير متوقع"):
                    updater.latest_sha("o/r", "main", "k")

    def test_download_asks_for_that_commit(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(b"zip")) as m:
            self.assertEqual(updater.download("o/r", "b" * 40, "k"), b"zip")
        self.assertEqual(m.call_args.args[0].full_url, f"https://api.github.com/repos/o/r/zipball/{'b' * 40}")

    def test_server_error_and_no_network_are_told_apart(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 502, "x", {}, None)):
            with self.assertRaisesRegex(updater.UpdateError, "502"):
                updater.latest_sha("o/r", "main", "k")
        for code in (401, 403, 404):
            with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", code, "x", {}, None)):
                with self.assertRaisesRegex(updater.UpdateError, "المفتاح"):
                    updater.latest_sha("o/r", "main", "k")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
            with self.assertRaisesRegex(updater.UpdateError, "لا يوجد اتصال"):
                updater.latest_sha("o/r", "main", "k")


class Members(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "manage.py").write_text("old", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def refused(self, data: bytes):
        with self.assertRaises(updater.UpdateError):
            updater.apply_zip(data, self.root)
        self.assertEqual((self.root / "manage.py").read_text(encoding="utf-8"), "old")
        releases = self.root / ".code-releases" / "releases"
        self.assertEqual(list(releases.iterdir()) if releases.exists() else [], [])

    def test_names_windows_would_change_are_refused(self):
        for name in ("app/b.py.", "app/b.py ", "app/dir./c.py", "app/COM1.log", "app/lpt9"):
            with self.subTest(name=name):
                self.refused(archive({**BASE, name: "x"}))

    def test_symlink_member_is_refused(self):
        self.refused(archive({**BASE, "app/link.py": "/etc/passwd"}, attrs={"app/link.py": 0o120777}))

    def test_reserved_release_manifest_is_refused(self):
        self.refused(archive({**BASE, ".release-source.json": "{}"}))
        self.refused(archive({**BASE, ".Release-Source.JSON": "{}"}))

    def test_unsafe_or_foreign_roots_are_refused(self):
        self.refused(archive(BASE, top=".."))
        self.refused(archive(BASE, top="c:"))
        self.refused(archive({"manage.py": "x", "lib/a.py": "x"}))          # no app/ folder
        self.refused(archive({"app/a.py": "x"}))                             # no manage.py

    def test_member_count_and_total_size_limits(self):
        many = {**BASE, **{f"app/m{i}.py": "" for i in range(15000)}}
        self.refused(archive(many))
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for name, text in BASE.items():
                z.writestr(f"t/{name}", text)
        with zipfile.ZipFile(io.BytesIO(b.getvalue())) as z:
            z.infolist()[0].file_size = 256 * 1024 * 1024 + 1                 # what the header claims
            with self.assertRaisesRegex(updater.UpdateError, "safety limit"):
                updater._members(z)

    def test_a_good_archive_still_stages(self):
        res = updater.apply_zip(archive({**BASE, "app/ok.name.py": "y"}), self.root)
        self.assertEqual(res["files"], 3)
        self.assertEqual((Path(res["release"]) / "app" / "ok.name.py").read_text(encoding="utf-8"), "y")


class Installed(unittest.TestCase):
    def test_active_release_state_wins_over_the_old_record(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            updater.record(data, "c" * 40, "test")
            with mock.patch.object(updater.releases, "read_state",
                                   return_value={"active": {"sha": "d" * 40, "ref": "develop"}, "pending": True}):
                self.assertEqual(updater.installed(data), {"sha": "d" * 40, "ref": "develop", "pending": True})
            with mock.patch.object(updater.releases, "read_state", return_value={}):
                self.assertEqual(updater.installed(data)["sha"], "c" * 40)
            (data / "installed_version.json").unlink()
            with mock.patch.object(updater.releases, "read_state", return_value={}):
                self.assertEqual(updater.installed(data), {})


if __name__ == "__main__":
    unittest.main()


class NoKey(unittest.TestCase):
    """The repository is public: a PC with no key file still updates (anonymous GitHub API)."""

    def _no_key_env(self, d):
        env = mock.patch.dict("os.environ", {"PHARM_UPDATE_TOKEN_FILE": str(Path(d) / "missing.txt")})
        return env

    def test_missing_or_empty_key_means_anonymous(self):
        with tempfile.TemporaryDirectory() as d:
            with self._no_key_env(d) as env:
                env.pop("PHARM_GITHUB_TOKEN", None)
                self.assertIsNone(updater.read_token())
                (Path(d) / "missing.txt").write_text(" \n", encoding="utf-8")
                self.assertIsNone(updater.read_token())

    def test_anonymous_request_sends_no_authorization(self):
        seen = []

        def fake(req, timeout):
            seen.append(req.get_header("Authorization"))
            return _Resp(b"c" * 40)
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.assertEqual(updater.latest_sha("o/r", "main", None), "c" * 40)
            self.assertEqual(updater.download("o/r", "c" * 40, None), b"c" * 40)
        self.assertEqual(seen, [None, None])

    def test_anonymous_private_repo_says_what_to_do(self):
        for code in (401, 404):
            with self.subTest(code=code), mock.patch(
                    "urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", code, "x", {}, None)):
                with self.assertRaisesRegex(updater.UpdateError, "بدون مفتاح.*github_token.txt"):
                    updater.latest_sha("o/r", "main", None)

    def test_anonymous_rate_limit_says_wait(self):
        for code in (403, 429):
            with self.subTest(code=code), mock.patch(
                    "urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", code, "x", {}, None)):
                with self.assertRaisesRegex(updater.UpdateError, "انتظر"):
                    updater.download("o/r", "d" * 40, None)

    def test_keyed_rate_limit_is_not_called_a_bad_key(self):
        hdrs = {"X-RateLimit-Remaining": "0"}
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 403, "x", hdrs, None)):
            with self.assertRaisesRegex(updater.UpdateError, "انتظر"):
                updater.latest_sha("o/r", "main", "k")
