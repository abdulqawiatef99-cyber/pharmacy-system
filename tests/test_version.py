"""The build that is tested on the devices carries the number that is released (RELEASE_RUNBOOK P4)."""
import re
import unittest

from app.version import __version__


class VersionTest(unittest.TestCase):
    def test_version_is_a_final_release_number(self):
        # No rc/dev suffix on develop while a release is being accepted: the trial installer and the
        # released one must report the same version. A new pre-release cycle changes this test on purpose.
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
