"""Safety properties of the portable acceptance runner, without nested suites."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts.validate_local_release import isolated_environment, validate


class TestReleaseValidation(unittest.TestCase):
    def setUp(self):
        platform_patch = patch('scripts.validate_local_release.platform.platform', return_value='synthetic-platform')
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def test_inherited_production_paths_and_printer_are_replaced(self):
        with patch.dict(os.environ, {'PHARM_DB_PATH': 'LIVE.db', 'PHARM_UPLOAD_DIR': 'LIVE',
                                     'PHARM_PRINTER': 'win:LIVE', 'ANTHROPIC_API_KEY': 'synthetic-key',
                                     'PHARM_LAUNCHER': '1'}):
            env = isolated_environment(Path('isolated'))
        self.assertEqual(env['PHARM_DB_PATH'], str(Path('isolated/synthetic.db')))
        self.assertEqual(env['PHARM_PRINTER'], 'none')
        self.assertEqual(env['ANTHROPIC_API_KEY'], '')
        self.assertNotIn('PHARM_LAUNCHER', env)

    def test_failure_stops_and_report_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'logs'
            with patch('scripts.validate_local_release.subprocess.run', return_value=subprocess.CompletedProcess([], 1)) as run:
                report = validate(Path(temporary), out)
            self.assertFalse(report['ok'])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(json.loads((out / 'report.json').read_text())['steps'][0]['returncode'], 1)

    def test_timeout_is_recorded_and_temporary_database_directory_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch('scripts.validate_local_release.subprocess.run', side_effect=subprocess.TimeoutExpired('synthetic', 1)) as run:
                report = validate(Path(temporary), Path(temporary) / 'logs', timeout=1)
            self.assertEqual(report['steps'][0]['error'], 'timeout')
            self.assertFalse(Path(run.call_args.kwargs['env']['PHARM_DATA_DIR']).exists())
            self.assertFalse(report['windows_hardware_accepted'])
