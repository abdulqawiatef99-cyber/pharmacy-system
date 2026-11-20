"""Part 3 item 0 (owner 20/11 23:18): which private key file on a flash drive matches which committed public key
(update-1 / update-2-spare / none), and a new key pair written to a folder the owner picks. The private key is
never printed, copied or kept anywhere else. Synthetic keys only."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.core import update_sign

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "windows"))
import signing_key_check as skc  # noqa: E402
import new_signing_key as nsk  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-keys-")).resolve()
        self.keys = self.tmp / "release_keys"
        self.keys.mkdir()
        self.p1, pub1 = update_sign.generate()
        self.p2, pub2 = update_sign.generate()
        (self.keys / "update-1.pub").write_text(pub1 + "\n", encoding="ascii")
        (self.keys / "update-2-spare.pub").write_text(pub2 + "\n", encoding="ascii")
        self.drive = self.tmp / "F"
        (self.drive / "old" / "deep").mkdir(parents=True)


class TestCheck(Base):
    def test_each_file_is_named_by_the_key_it_matches(self):
        (self.drive / "update-signing-PRIVATE.txt").write_text(self.p1 + "\n", encoding="ascii")
        (self.drive / "old" / "spare.txt").write_text("  " + self.p2 + "\r\n", encoding="ascii")
        (self.drive / "old" / "deep" / "other.txt").write_text(update_sign.generate()[0], encoding="ascii")
        (self.drive / "notes.txt").write_text("مش مفتاح\n", encoding="utf-8")
        (self.drive / "big.txt").write_bytes(b"x" * 10000)
        found = {Path(f).name: k for f, k in skc.scan([self.drive], skc.load_keys(self.keys))}
        self.assertEqual(found, {"update-signing-PRIVATE.txt": "update-1.pub", "spare.txt": "update-2-spare.pub",
                                 "other.txt": None})

    def test_output_never_contains_a_private_key_and_exit_code_says_the_outcome(self):
        (self.drive / "a.txt").write_text(self.p2, encoding="ascii")
        r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "windows" / "signing_key_check.py"),
                            "--keys", str(self.keys), str(self.drive)], capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)                   # spare only
        self.assertNotIn(self.p2, r.stdout + r.stderr)
        self.assertIn("update-2-spare", r.stdout)
        (self.drive / "b.txt").write_text(self.p1, encoding="ascii")
        r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "windows" / "signing_key_check.py"),
                            "--keys", str(self.keys), str(self.drive)], capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0)                                         # update-1 found
        self.assertNotIn(self.p1, r.stdout + r.stderr)

    def test_nothing_found(self):
        self.assertEqual(skc.main(["--keys", str(self.keys), str(self.drive)]), 2)

    def test_repository_keys_are_found_next_to_a_downloaded_copy(self):
        self.assertEqual(skc.default_keys_dir(ROOT / "scripts" / "windows"), ROOT / "release_keys")


class TestNewKey(Base):
    def test_new_pair_written_once_and_private_never_printed(self):
        out = self.drive / "new"
        r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "windows" / "new_signing_key.py"), str(out)],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        priv = (out / "update-signing-PRIVATE.txt").read_text(encoding="ascii").strip()
        pub = (out / "update-3.pub").read_text(encoding="ascii").strip()
        self.assertNotIn(priv, r.stdout + r.stderr)
        self.assertEqual(len(base64.b64decode(priv)), 32)
        msg = b"check"
        update_sign.verify(msg, update_sign.sign(msg, priv), [base64.b64decode(pub)])
        self.assertEqual(nsk.main([str(out)]), 1)                                 # never overwrites
        self.assertEqual((out / "update-signing-PRIVATE.txt").read_text(encoding="ascii").strip(), priv)


class TestToolsFolder(unittest.TestCase):
    def test_both_tools_and_the_public_keys_reach_the_tools_folder(self):
        from app.core import tools_folder
        with tempfile.TemporaryDirectory() as tmp:
            out = tools_folder.refresh(ROOT, Path(tmp), desktop=Path(tmp))
            names = set(os.listdir(Path(tmp) / "tools"))
            self.assertLessEqual({"signing-key-check.bat", "signing_key_check.py", "new-signing-key.bat",
                                  "new_signing_key.py"}, names, out)
            self.assertLessEqual({"update-1.pub", "update-2-spare.pub"}, set(os.listdir(Path(tmp) / "tools" / "keys")))


if __name__ == "__main__":
    unittest.main()
