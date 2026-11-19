"""Signed releases (app/core/release_signature.py, scripts/release/sign_release.py): an update is
accepted only when the publisher signed exactly these files, for this channel, and it is not older
than what is installed. Keys are made inside the tests. The updater does not use this yet."""
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import release_signature as rs
from app.core import signed_envelope as se

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "release"))
import sign_release as cli  # noqa: E402
import keyfile  # noqa: E402  (scripts/license, put on the path by sign_release)

T0 = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40
PHRASE = "tests only: horse battery staple"
FILES = {"manage.py": "print('program')\n", "release_protocol.json": '{"protocol": 1}', "app/db.py": "x = 1\n",
         "app/version.py": '__version__ = "0.4.1"\n', "app/web/index.html": "<html>صيدلية</html>",
         "app/migrations/0001_init.sql": "CREATE TABLE t(x);\n"}


def write_tree(root: Path, files: dict = FILES) -> Path:
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()
        self.tree = write_tree(self.dir / "release")
        self.key = Ed25519PrivateKey.generate()
        self.trusted = [se.public_key_bytes(self.key)]

    def manifest(self, **kw) -> rs.Manifest:
        args = dict(channel="main", version="0.4.1", commit=COMMIT, sequence=100, released_at=T0, protocol=1)
        args.update(kw)
        return rs.build_manifest(self.tree, **args)

    def code(self, fn, *args, **kw):
        with self.assertRaises(rs.ReleaseSignatureError) as cm:
            fn(*args, **kw)
        return cm.exception.code

    def forged(self, **changes) -> bytes:
        payload = self.manifest().payload()
        for k, v in changes.items():
            if v is ...:
                payload.pop(k)
            else:
                payload[k] = v
        return se.sign(se.canonical_json(payload), rs.PURPOSE, self.key)


class TestSignature(Base):
    def test_round_trip(self):
        m = self.manifest()
        got = rs.verify_manifest(rs.sign_manifest(m, self.key), self.trusted)
        self.assertEqual(got, m)
        self.assertEqual(set(got.files), set(FILES))
        self.assertEqual(got.files["manage.py"], hashlib.sha256(FILES["manage.py"].encode()).hexdigest())

    def test_this_build_has_no_release_key_yet_so_it_refuses_every_signed_release(self):
        self.assertEqual(rs.RELEASE_KEYS, ())
        self.assertEqual(self.code(rs.verify_manifest, rs.sign_manifest(self.manifest(), self.key)), "no_trusted_key")

    def test_built_in_keys_are_public_keys_in_the_documented_form(self):
        for k in rs.RELEASE_KEYS:
            self.assertRegex(k, r"^[0-9a-f]{64}$")
        self.assertEqual(len(set(rs.RELEASE_KEYS)), len(rs.RELEASE_KEYS))

    def test_a_release_signed_by_anyone_else_is_refused(self):
        signed = rs.sign_manifest(self.manifest(), Ed25519PrivateKey.generate())
        self.assertEqual(self.code(rs.verify_manifest, signed, self.trusted), "untrusted_key")

    def test_a_licence_key_signature_is_not_a_release_signature(self):
        """Even the right key, used for another purpose, does not make a release."""
        as_license = se.sign(se.canonical_json(self.manifest().payload()), "license", self.key)
        self.assertEqual(self.code(rs.verify_manifest, as_license, self.trusted), "wrong_purpose")

    def test_changing_one_file_hash_in_a_signed_manifest_is_refused(self):
        import base64
        env = json.loads(rs.sign_manifest(self.manifest(), self.key))
        payload = json.loads(base64.b64decode(env["payload"]))
        payload["files"]["app/db.py"] = hashlib.sha256(b"evil").hexdigest()
        env["payload"] = base64.b64encode(se.canonical_json(payload)).decode()
        self.assertEqual(self.code(rs.verify_manifest, json.dumps(env).encode(), self.trusted), "bad_signature")

    def test_key_change_over_two_signatures(self):
        new = Ed25519PrivateKey.generate()
        signed = rs.sign_manifest(self.manifest(), self.key, new)
        for trusted in (self.trusted, [se.public_key_bytes(new)]):
            self.assertEqual(rs.verify_manifest(signed, trusted).sequence, 100)


class TestStrictManifest(Base):
    def test_a_newer_format_or_another_product_is_named_as_such(self):
        self.assertEqual(self.code(rs.verify_manifest, self.forged(format=2), self.trusted), "unsupported_format")
        self.assertEqual(self.code(rs.verify_manifest, self.forged(product="other"), self.trusted), "wrong_product")

    def test_malformed_manifests_are_refused_even_when_signed(self):
        ok = self.manifest().payload()["files"]
        sha = "0" * 64
        bad = [dict(extra=1), dict(files=...), dict(channel="beta"), dict(version="v1"), dict(version=""),
               dict(commit="abc"), dict(commit="A" * 40), dict(sequence=0), dict(sequence=-5), dict(sequence="7"),
               dict(sequence=True), dict(sequence=2 ** 53), dict(protocol=0), dict(released_at="yesterday"),
               dict(released_at="2026-02-30T00:00:00Z"), dict(archive_sha256="xyz"), dict(archive_sha256=5),
               dict(files={}), dict(files=[]), dict(files={**ok, "app/x.py": "short"}), dict(files={**ok, "app/x.py": 5}),
               dict(files={"readme.txt": sha}),                                  # not this program
               dict(files={"manage.py": sha}),                                   # no app/
               dict(files={**ok, "../evil.py": sha}), dict(files={**ok, "/etc/passwd": sha}),
               dict(files={**ok, "app\\evil.py": sha}), dict(files={**ok, "C:/evil.py": sha}),
               dict(files={**ok, "app//x.py": sha}), dict(files={**ok, "app/./x.py": sha}),
               dict(files={**ok, "app/x.py.": sha}), dict(files={**ok, "app/x.py ": sha}),
               dict(files={**ok, "": sha}), dict(files={**ok, "x" * 401: sha}),
               dict(files={**ok, "APP/DB.PY": sha}),                             # same file on Windows
               dict(files={**ok, "data/pharmacy.db": sha}), dict(files={**ok, "Backups/x.zip": sha}),
               dict(files={**ok, ".venv/x": sha}), dict(files={**ok, ".git/config": sha}),
               dict(files={**ok, ".release-source.json": sha})]
        for changes in bad:
            self.assertEqual(self.code(rs.verify_manifest, self.forged(**changes), self.trusted), "malformed", changes)

    def test_the_signing_side_refuses_what_the_program_would_refuse(self):
        m = self.manifest()
        for broken in (rs.Manifest(**{**m.__dict__, "channel": "beta"}),
                       rs.Manifest(**{**m.__dict__, "files": {"readme.txt": "0" * 64}}),
                       rs.Manifest(**{**m.__dict__, "sequence": 0})):
            with self.assertRaises(rs.ReleaseSignatureError):
                rs.sign_manifest(broken, self.key)

    def test_a_tree_holding_pharmacy_data_folders_is_not_a_release(self):
        for name in ("data/pharmacy.db", "backups/b.zip", ".venv/pyvenv.cfg"):
            tree = write_tree(self.dir / ("t-" + name.split("/")[0].strip(".")), {**FILES, name: "x"})
            with self.assertRaises(rs.ReleaseSignatureError):
                rs.build_manifest(tree, channel="main", version="1.0", commit=COMMIT, sequence=1, released_at=T0, protocol=1)

    def test_naive_release_time_is_refused(self):
        with self.assertRaises(ValueError):
            self.manifest(released_at=datetime(2026, 11, 19))


class TestPolicy(Base):
    def test_the_right_channel_protocol_and_a_newer_or_equal_sequence_pass(self):
        m = self.manifest(sequence=100)
        rs.check_policy(m, channel="main", protocol=1)
        rs.check_policy(m, channel="main", protocol=1, installed_sequence=99)
        rs.check_policy(m, channel="main", protocol=1, installed_sequence=100)

    def test_a_release_of_another_channel_is_refused(self):
        """A test build signed for develop must never install on a pharmacy that follows main."""
        self.assertEqual(self.code(rs.check_policy, self.manifest(channel="develop"), channel="main", protocol=1),
                         "wrong_channel")

    def test_an_older_release_is_refused(self):
        """A genuine old release with a known hole must not be replayed."""
        self.assertEqual(self.code(rs.check_policy, self.manifest(sequence=100), channel="main", protocol=1,
                                   installed_sequence=101), "downgrade")

    def test_another_protocol_is_refused(self):
        self.assertEqual(self.code(rs.check_policy, self.manifest(protocol=2), channel="main", protocol=1),
                         "wrong_protocol")


class TestFiles(Base):
    def test_the_signed_tree_passes(self):
        m = self.manifest()
        rs.check_tree(m, self.tree)
        rs.check_files(m, rs.hash_tree(self.tree))

    def test_the_updaters_own_staging_file_is_not_part_of_the_release(self):
        m = self.manifest()
        (self.tree / rs.STAGING_FILE).write_text("{}", encoding="utf-8")
        rs.check_tree(m, self.tree)

    def test_a_changed_file_is_refused_and_named(self):
        m = self.manifest()
        (self.tree / "app" / "db.py").write_text("x = 2  # back door\n", encoding="utf-8")
        with self.assertRaises(rs.ReleaseSignatureError) as cm:
            rs.check_tree(m, self.tree)
        self.assertEqual(cm.exception.code, "files_mismatch")
        self.assertIn("changed: app/db.py", str(cm.exception))

    def test_one_changed_byte_even_a_line_end_is_refused(self):
        m = self.manifest()
        (self.tree / "manage.py").write_bytes(FILES["manage.py"].encode().replace(b"\n", b"\r\n"))
        self.assertEqual(self.code(rs.check_tree, m, self.tree), "files_mismatch")

    def test_an_added_file_is_refused(self):
        m = self.manifest()
        (self.tree / "app" / "sitecustomize.py").write_text("import os\n", encoding="utf-8")
        with self.assertRaises(rs.ReleaseSignatureError) as cm:
            rs.check_tree(m, self.tree)
        self.assertIn("not signed: app/sitecustomize.py", str(cm.exception))

    def test_a_missing_file_is_refused(self):
        m = self.manifest()
        (self.tree / "app" / "migrations" / "0001_init.sql").unlink()
        with self.assertRaises(rs.ReleaseSignatureError) as cm:
            rs.check_tree(m, self.tree)
        self.assertIn("missing: app/migrations/0001_init.sql", str(cm.exception))

    @unittest.skipIf(os.name == "nt", "creating a link needs a privilege on Windows")
    def test_a_link_is_refused(self):
        m = self.manifest()
        (self.tree / "app" / "link.py").symlink_to(self.tree / "manage.py")
        self.assertEqual(self.code(rs.check_tree, m, self.tree), "files_mismatch")

    def test_same_shape_as_the_updaters_own_file_list(self):
        """The staged release's .release-source.json "files" compares directly (the later wiring)."""
        from app.core import updater
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, text in FILES.items():
                z.writestr(f"owner-repo-abc/{name}", text)
        staged = updater.apply_zip(buf.getvalue(), self.dir / "program", code_home=self.dir / "code")
        listed = json.loads((Path(staged["release"]) / rs.STAGING_FILE).read_text(encoding="utf-8"))["files"]
        m = self.manifest(archive=buf.getvalue())
        rs.check_files(m, listed)
        rs.check_tree(m, Path(staged["release"]))
        rs.check_archive(m, buf.getvalue())

    def test_archive_check(self):
        data = b"the exact download"
        m = self.manifest(archive=data)
        rs.check_archive(m, data)
        self.assertEqual(self.code(rs.check_archive, m, data + b"!"), "archive_mismatch")
        self.assertEqual(self.code(rs.check_archive, self.manifest(), data), "archive_mismatch")   # none named


class TestTool(Base):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {keyfile.PASSPHRASE_ENV: PHRASE})
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main([str(a) for a in argv])
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def keygen(self, name="release.key"):
        path = self.dir / "keys" / name
        code, out, err = self.run_cli("keygen", "--out", path)
        self.assertEqual(code, 0, err)
        return path, [line.split(": ")[1] for line in out.splitlines() if line.startswith("public key")][0]

    def test_manifest_then_sign_then_verify_a_folder(self):
        key, public = self.keygen()
        manifest, signed = self.dir / "manifest.json", self.dir / "release.sig.json"
        code, out, err = self.run_cli("manifest", "--tree", self.tree, "--channel", "main", "--commit", COMMIT,
                                      "--sequence", 7, "--out", manifest)
        self.assertEqual(code, 0, err)
        plain = json.loads(manifest.read_text(encoding="utf-8"))             # readable before anything is signed
        self.assertEqual((plain["version"], plain["protocol"], plain["sequence"]), ("0.4.1", 1, 7))
        code, out, err = self.run_cli("sign", "--key", key, "--manifest", manifest, "--out", signed)
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("verify", "--public-key", public, "--signed", signed, "--tree", self.tree,
                                      "--channel", "main", "--installed-sequence", 6)
        self.assertEqual(code, 0, err)
        for line in ("signature: OK", "channel and sequence: OK", "files: OK"):
            self.assertIn(line, out)
        # the customer side reads the same file
        self.assertEqual(rs.verify_manifest(signed.read_bytes(), [public]).files, plain["files"])

    def test_verify_fails_loudly_on_a_changed_tree_wrong_channel_old_sequence_or_wrong_key(self):
        key, public = self.keygen()
        manifest, signed = self.dir / "manifest.json", self.dir / "release.sig.json"
        self.run_cli("manifest", "--tree", self.tree, "--channel", "main", "--commit", COMMIT, "--sequence", 7,
                     "--out", manifest)
        self.run_cli("sign", "--key", key, "--manifest", manifest, "--out", signed)
        base = ("verify", "--public-key", public, "--signed", signed)
        code, _, err = self.run_cli(*base, "--channel", "develop")
        self.assertEqual((code, "wrong_channel" in err), (2, True))
        code, _, err = self.run_cli(*base, "--channel", "main", "--installed-sequence", 8)
        self.assertEqual((code, "downgrade" in err), (2, True))
        (self.tree / "manage.py").write_text("changed", encoding="utf-8")
        code, _, err = self.run_cli(*base, "--tree", self.tree)
        self.assertEqual((code, "files_mismatch" in err), (2, True))
        _, other = self.keygen("other.key")
        code, _, err = self.run_cli("verify", "--public-key", other, "--signed", signed)
        self.assertEqual((code, "untrusted_key" in err), (2, True))

    def test_an_edited_manifest_that_would_be_refused_is_not_signed(self):
        key, _ = self.keygen()
        manifest, signed = self.dir / "manifest.json", self.dir / "release.sig.json"
        self.run_cli("manifest", "--tree", self.tree, "--channel", "main", "--commit", COMMIT, "--sequence", 7,
                     "--out", manifest)
        plain = json.loads(manifest.read_text(encoding="utf-8"))
        plain["files"]["../evil.py"] = "0" * 64
        manifest.write_text(json.dumps(plain), encoding="utf-8")
        code, _, err = self.run_cli("sign", "--key", key, "--manifest", manifest, "--out", signed)
        self.assertEqual(code, 2)
        self.assertFalse(signed.exists())

    def test_signing_needs_the_key_outside_the_repository_and_the_passphrase(self):
        key, _ = self.keygen()
        manifest, signed = self.dir / "manifest.json", self.dir / "release.sig.json"
        self.run_cli("manifest", "--tree", self.tree, "--channel", "main", "--commit", COMMIT, "--sequence", 7,
                     "--out", manifest)
        code, _, err = self.run_cli("sign", "--key", ROOT / "release.key", "--manifest", manifest, "--out", signed)
        self.assertEqual((code, "inside a git repository" in err), (2, True))
        with mock.patch.dict(os.environ, {keyfile.PASSPHRASE_ENV: "another passphrase!"}):
            code, _, err = self.run_cli("sign", "--key", key, "--manifest", manifest, "--out", signed)
        self.assertEqual(code, 2)
        self.assertFalse(signed.exists())
        code, _, err = self.run_cli("keygen", "--out", ROOT / "scripts" / "release" / "release.key")
        self.assertEqual(code, 2)
        self.assertFalse((ROOT / "scripts" / "release" / "release.key").exists())

    def test_a_signed_file_is_never_overwritten(self):
        key, _ = self.keygen()
        manifest, signed = self.dir / "manifest.json", self.dir / "release.sig.json"
        self.run_cli("manifest", "--tree", self.tree, "--channel", "main", "--commit", COMMIT, "--sequence", 7,
                     "--out", manifest)
        self.assertEqual(self.run_cli("sign", "--key", key, "--manifest", manifest, "--out", signed)[0], 0)
        before = signed.read_bytes()
        self.assertEqual(self.run_cli("sign", "--key", key, "--manifest", manifest, "--out", signed)[0], 2)
        self.assertEqual(signed.read_bytes(), before)

    def test_tree_needs_commit_and_sequence(self):
        code, _, err = self.run_cli("manifest", "--tree", self.tree, "--channel", "main", "--out", self.dir / "m.json")
        self.assertEqual(code, 2)
        self.assertFalse((self.dir / "m.json").exists())

    @unittest.skipUnless((ROOT / ".git").exists(), "needs the git checkout")
    def test_from_git_describes_exactly_what_git_archive_contains(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        if not head or subprocess.run(["git", "cat-file", "-e", f"{head}^{{commit}}"], cwd=ROOT).returncode != 0:
            self.skipTest("no commit to describe")
        manifest = self.dir / "manifest.json"
        code, out, err = self.run_cli("manifest", "--from-git", head, "--channel", "develop", "--out", manifest)
        self.assertEqual(code, 0, err)
        plain = json.loads(manifest.read_text(encoding="utf-8"))
        tracked = subprocess.run(["git", "ls-tree", "-r", "--name-only", "-z", head], cwd=ROOT, capture_output=True
                                 ).stdout.decode("utf-8").split("\0")
        self.assertEqual(sorted(plain["files"]), sorted(t for t in tracked if t))
        blob = subprocess.run(["git", "-c", "core.autocrlf=false", "show", f"{head}:manage.py"], cwd=ROOT,
                              capture_output=True).stdout
        self.assertEqual(plain["files"]["manage.py"], hashlib.sha256(blob).hexdigest())
        self.assertEqual(plain["commit"], head)
        self.assertGreaterEqual(plain["sequence"], 1)
        self.assertRegex(plain["version"], r"^\d+\.\d+")
        rs.from_payload(plain)                                               # and the program would accept it


class TestNotWiredYet(unittest.TestCase):
    def test_the_updater_and_the_bootstrap_do_not_ask_for_a_signature_yet(self):
        """Launch freeze: this batch changes nothing the program does. Wiring changes this test on purpose."""
        program = [p for p in ROOT.glob("app/**/*.py") if p.name != "release_signature.py"]
        program += [ROOT / "manage.py", ROOT / "launcher.py", ROOT / "release_bootstrap.py"]
        for p in program:
            self.assertNotIn("release_signature", p.read_text(encoding="utf-8"), p.relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
