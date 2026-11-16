"""The perf scripts must not set TZ=Africa/Cairo on Windows (#259).

The Windows C runtime reads TZ in its own format; an IANA name there can turn local time into UTC for the
script and for the server it starts. The pharmacy PC already runs on Cairo time.
"""
import unittest
from pathlib import Path

PERF = Path(__file__).resolve().parents[1] / "scripts" / "perf"
SCRIPTS = ("real_years.py", "loadgen.py", "bench.py", "serve_at.py")


class TestNoTzOnWindows(unittest.TestCase):
    def test_every_tz_override_is_guarded(self):
        for name in SCRIPTS:
            lines = (PERF / name).read_text(encoding="utf-8").splitlines()
            hits = [i for i, l in enumerate(lines) if 'setdefault("TZ"' in l]
            self.assertEqual(len(hits), 1, name)
            self.assertTrue(lines[hits[0] - 1].startswith('if os.name != "nt":'), name)

    def test_on_windows_the_block_leaves_tz_alone(self):
        # run each script's own TZ block with a fake os whose name is "nt" (Windows) and then "posix"
        import types
        for name in SCRIPTS:
            lines = (PERF / name).read_text(encoding="utf-8").splitlines()
            i = next(k for k, l in enumerate(lines) if l.startswith('if os.name != "nt":'))
            block = "\n".join(lines[i:i + 2])
            for osname, expected in (("nt", None), ("posix", "Africa/Cairo")):
                fake = types.SimpleNamespace(name=osname, environ={})
                exec(block, {"os": fake})
                self.assertEqual(fake.environ.get("TZ"), expected, (name, osname))


if __name__ == "__main__":
    unittest.main()
