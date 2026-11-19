"""Publisher's tool (scripts/license/issue_license.py): keys stay outside every repository and behind
a passphrase, every issued licence is recorded, and what it issues opens on the customer side.
Everything happens in a temporary folder with keys made on the spot."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "license"))
import issue_license as cli  # noqa: E402
import keyfile  # noqa: E402

from app.licensing import fingerprint as fp, license as L  # noqa: E402

PHRASE = "tests only: horse battery staple"
PC_A = fp.from_raw({"board": "4c4c4544-0042-3510-8051-c7c04f4a3432", "system": "guid-a", "disk": "a4f1c2d3"})
PC_B = fp.from_raw({"board": "9f2d1a70-11c3-4e57-a2b8-0d6f5e3c7a91", "system": "guid-b", "disk": "b5e2d3c4"})
WEAK = fp.from_raw({"system": "guid-w", "disk": "c6f3e4d5"})


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {keyfile.PASSPHRASE_ENV: PHRASE})
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main([str(a) for a in argv])
            except SystemExit as e:                   # argparse and our own "refused" exits
                code = e.code if isinstance(e.code, int) else 1
                if not isinstance(e.code, int) and e.code:
                    err.write(str(e.code))
        return code, out.getvalue(), err.getvalue()

    def keygen(self, name="license.key") -> tuple[Path, str]:
        path = self.dir / "keys" / name
        code, out, err = self.run_cli("keygen", "--out", path)
        self.assertEqual(code, 0, err)
        public = [line.split(": ")[1] for line in out.splitlines() if line.startswith("public key")][0]
        return path, public


class TestKeys(Base):
    def test_keygen_writes_an_encrypted_key_and_prints_only_the_public_half(self):
        path, public = self.keygen()
        pem = path.read_bytes()
        self.assertIn(b"ENCRYPTED PRIVATE KEY", pem)
        self.assertRegex(public, r"^[0-9a-f]{64}$")
        code, out, _ = self.run_cli("public", "--key", path)
        self.assertEqual(code, 0)
        self.assertIn(public, out)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o077, 0)          # nobody else on the PC can read it

    def test_a_key_is_never_written_inside_a_repository(self):
        for inside in (ROOT / "license.key", ROOT / "docs" / "license.key", ROOT / "scripts" / "license" / "k.key"):
            code, _, err = self.run_cli("keygen", "--out", inside)
            self.assertEqual(code, 2)
            self.assertIn("inside a git repository", err)
            self.assertFalse(inside.exists())
        other_repo = self.dir / "other"
        (other_repo / ".git").mkdir(parents=True)
        code, _, err = self.run_cli("keygen", "--out", other_repo / "sub" / "k.key")
        self.assertEqual(code, 2)
        self.assertFalse((other_repo / "sub").exists())

    def test_a_key_inside_a_repository_is_not_used_either(self):
        path, _ = self.keygen()
        (path.parent / ".git").mkdir()                                # the folder became a repository later
        code, _, err = self.run_cli("public", "--key", path)
        self.assertEqual(code, 2)
        self.assertIn("inside a git repository", err)

    def test_an_existing_key_is_never_overwritten(self):
        path, _ = self.keygen()
        before = path.read_bytes()
        code, _, err = self.run_cli("keygen", "--out", path)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(path.read_bytes(), before)

    def test_wrong_or_short_passphrase(self):
        path, _ = self.keygen()
        with mock.patch.dict(os.environ, {keyfile.PASSPHRASE_ENV: "another passphrase!"}):
            code, _, err = self.run_cli("public", "--key", path)
            self.assertEqual(code, 2)
            self.assertIn("wrong passphrase", err)
        with mock.patch.dict(os.environ, {keyfile.PASSPHRASE_ENV: "short"}):
            code, _, err = self.run_cli("keygen", "--out", self.dir / "keys" / "second.key")
            self.assertEqual(code, 2)
            self.assertFalse((self.dir / "keys" / "second.key").exists())

    def test_the_passphrase_is_asked_twice_on_the_keyboard_and_must_agree(self):
        os.environ.pop(keyfile.PASSPHRASE_ENV)
        with mock.patch.object(keyfile.getpass, "getpass", side_effect=[PHRASE, PHRASE + "x"]):
            code, _, err = self.run_cli("keygen", "--out", self.dir / "keys" / "typed.key")
        self.assertEqual(code, 2)
        self.assertIn("differ", err)
        with mock.patch.object(keyfile.getpass, "getpass", side_effect=[PHRASE, PHRASE]):
            code, _, _ = self.run_cli("keygen", "--out", self.dir / "keys" / "typed.key")
        self.assertEqual(code, 0)

    def test_the_passphrase_cannot_be_given_on_the_command_line(self):
        for flag in ("--passphrase", "--password"):
            code, _, _ = self.run_cli("keygen", "--out", self.dir / "k.key", flag, PHRASE)
            self.assertEqual(code, 2)                                 # argparse: unknown argument
        self.assertFalse((self.dir / "k.key").exists())

    def test_missing_key_file(self):
        code, _, err = self.run_cli("public", "--key", self.dir / "nope.key")
        self.assertEqual(code, 2)
        self.assertIn("not readable", err)


class TestIssue(Base):
    def setUp(self):
        super().setUp()
        self.key, self.public = self.keygen()
        self.req_a = fp.encode_request(PC_A, "CASHIER")
        self.req_b = fp.encode_request(PC_B, "HEIKAL")
        self.out = self.dir / "out" / "license.lic"

    def issue(self, *extra, out=None):
        return self.run_cli("issue", "--key", self.key, "--customer", "صيدلية تجربة", "--out", out or self.out, *extra)

    def ledger(self) -> list[dict]:
        return cli.read_ledger(self.key.parent / cli.LEDGER_NAME)

    def test_subscription_for_two_pcs_opens_on_the_customer_side(self):
        code, out, err = self.issue("--kind", "subscription", "--days", "365", "--request", self.req_a,
                                    "--request", self.req_b, "--starts", "2026-12-01")
        self.assertEqual(code, 0, err)
        lic = L.verify(self.out.read_bytes(), [self.public])
        self.assertEqual((lic.kind, lic.customer, lic.grace_days), ("subscription", "صيدلية تجربة", 14))
        self.assertEqual(lic.starts_at, datetime(2026, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(lic.expires_at, datetime(2027, 11, 30, 23, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(lic.devices, (PC_A, PC_B))
        self.assertRegex(lic.license_id, r"^PH-\d{8}-[0-9A-F]{6}$")
        now = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(L.evaluate(lic, now=now, device=PC_B).state, "valid")
        self.assertEqual(L.evaluate(lic, now=now, device=WEAK).state, "wrong_device")

    def test_perpetual_for_the_owners_pharmacy(self):
        code, _, err = self.issue("--kind", "perpetual", "--request", self.req_a)
        self.assertEqual(code, 0, err)
        lic = L.verify(self.out.read_bytes(), [self.public])
        self.assertEqual((lic.kind, lic.expires_at, lic.grace_days), ("perpetual", None, 0))
        far = datetime(2060, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(L.evaluate(lic, now=far, device=PC_A).usable)

    def test_expires_date_includes_its_last_day_and_grace_can_be_set(self):
        code, _, err = self.issue("--kind", "subscription", "--expires", "2027-03-31", "--starts", "2026-12-01",
                                  "--grace-days", "30", "--request", self.req_a)
        self.assertEqual(code, 0, err)
        lic = L.verify(self.out.read_bytes(), [self.public])
        self.assertEqual(lic.expires_at, datetime(2027, 3, 31, 23, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(lic.grace_days, 30)

    def test_untied_trial_is_allowed_and_short(self):
        code, _, err = self.issue("--kind", "trial", "--days", "14")
        self.assertEqual(code, 0, err)
        lic = L.verify(self.out.read_bytes(), [self.public])
        self.assertEqual((lic.kind, lic.devices, lic.grace_days), ("trial", (), 0))
        self.assertEqual(lic.expires_at - lic.starts_at, timedelta(days=14, seconds=-1))

    def test_terms_that_must_never_be_issued_write_nothing(self):
        refused = [("--kind", "subscription", "--days", "365"),                              # tied to no PC
                   ("--kind", "perpetual"),                                                 # same
                   ("--kind", "perpetual", "--days", "30", "--request", self.req_a),        # perpetual with an end
                   ("--kind", "subscription", "--request", self.req_a),                     # no end date
                   ("--kind", "subscription", "--days", "30", "--expires", "2027-01-01", "--request", self.req_a),
                   ("--kind", "trial", "--days", "60"),                                     # too long for a trial
                   ("--kind", "subscription", "--days", "30", "--request", self.req_a, "--request", self.req_a),
                   ("--kind", "subscription", "--days", "30", "--request", "PHREQ1-AAAA-00000000"),
                   ("--kind", "subscription", "--days", "30", "--request", self.req_a, "--grace-days", "200"),
                   ("--kind", "subscription", "--expires", "31/12/2027", "--request", self.req_a)]
        for args in refused:
            code, _, err = self.issue(*args)
            self.assertNotEqual(code, 0, args)
            self.assertFalse(self.out.exists(), args)
        self.assertEqual(self.ledger(), [])

    def test_every_issued_licence_is_recorded_next_to_the_key(self):
        self.issue("--kind", "subscription", "--days", "30", "--request", self.req_a)
        self.issue("--kind", "perpetual", "--request", self.req_b, out=self.dir / "out" / "second.lic")
        rows = self.ledger()
        self.assertEqual([r["kind"] for r in rows], ["subscription", "perpetual"])
        self.assertEqual(rows[0]["labels"], ["CASHIER"])
        self.assertEqual(rows[0]["devices"], [PC_A.as_json()])
        self.assertEqual(rows[1]["file"], "second.lic")
        self.assertEqual(len({r["license_id"] for r in rows}), 2)
        text = (self.key.parent / cli.LEDGER_NAME).read_text(encoding="utf-8")
        self.assertIn("صيدلية تجربة", text)
        self.assertNotIn("PRIVATE KEY", text)

    def test_a_pc_already_licensed_to_someone_else_gives_a_warning(self):
        self.issue("--kind", "perpetual", "--request", self.req_a)
        code, _, err = self.issue("--kind", "perpetual", "--request", self.req_a, out=self.dir / "out" / "renew.lic")
        self.assertEqual((code, err), (0, ""))                        # the same customer again: a renewal
        code, _, err = self.run_cli("issue", "--key", self.key, "--customer", "صيدلية تانية", "--kind", "perpetual",
                                    "--request", self.req_a, "--request", self.req_b, "--out", self.dir / "out" / "b.lic")
        self.assertEqual(code, 0)
        self.assertEqual(err.count("already has a licence issued to another customer: صيدلية تجربة"), 1)

    def test_a_weak_request_is_marked_for_the_publisher(self):
        weak = fp.encode_request(WEAK, "OLD-PC")
        code, out, _ = self.run_cli("request", weak)
        self.assertEqual(code, 0)
        self.assertIn("WEAK", out)
        self.issue("--kind", "perpetual", "--request", weak)
        self.assertEqual(self.ledger()[0]["labels"], ["OLD-PC (weak: no board id)"])
        code, out, _ = self.run_cli("request", self.req_a)
        self.assertIn("binding: board", out)

    def test_an_existing_licence_file_is_never_overwritten_and_never_lands_in_the_repository(self):
        self.issue("--kind", "perpetual", "--request", self.req_a)
        before = self.out.read_bytes()
        code, _, err = self.issue("--kind", "perpetual", "--request", self.req_a)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.out.read_bytes(), before)
        code, _, err = self.issue("--kind", "perpetual", "--request", self.req_a, out=ROOT / "license.lic")
        self.assertEqual(code, 2)
        self.assertFalse((ROOT / "license.lic").exists())
        self.assertEqual(len(self.ledger()), 1)

    def test_wrong_passphrase_issues_nothing_and_records_nothing(self):
        with mock.patch.dict(os.environ, {keyfile.PASSPHRASE_ENV: "another passphrase!"}):
            code, _, _ = self.issue("--kind", "perpetual", "--request", self.req_a)
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.ledger(), [])

    def test_show_reads_a_licence_and_checks_the_signature_when_asked(self):
        self.issue("--kind", "perpetual", "--request", self.req_a)
        code, out, _ = self.run_cli("show", self.out, "--public-key", self.public)
        self.assertEqual(code, 0)
        self.assertIn("signature: OK", out)
        self.assertEqual(json.loads(out[out.index("{"):])["customer"], "صيدلية تجربة")
        code, out, _ = self.run_cli("show", self.out)
        self.assertIn("NOT CHECKED", out)
        _, other_public = self.keygen("other.key")
        code, _, err = self.run_cli("show", self.out, "--public-key", other_public)
        self.assertEqual(code, 2)
        self.assertIn("untrusted_key", err)

    def test_show_on_a_file_that_is_not_a_licence_is_a_clean_error(self):
        for content in (b"{}", b"[]", b"junk", b'{"payload": 5}', b'{"payload": "@@"}'):
            bad = self.dir / "bad.lic"
            bad.write_bytes(content)
            code, _, err = self.run_cli("show", bad)
            self.assertEqual(code, 2, content)
            self.assertIn("malformed", err)

    def test_the_key_never_appears_in_what_the_tool_prints(self):
        code, out, err = self.issue("--kind", "perpetual", "--request", self.req_a)
        for text in (out, err, self.out.read_text(encoding="ascii")):
            self.assertNotIn("PRIVATE", text)
            self.assertNotIn(PHRASE, text)


if __name__ == "__main__":
    unittest.main()
