# Portable local-release validator

Branch codex/local-release-validation, baseline main 69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0. This is a missing delivery tool, not a replacement for existing pharmacy functions.

Run with the already installed project Python and dependencies:

```
python scripts/validate_local_release.py --output PATH_TO_DEDICATED_CHECK_LOG_FOLDER
```

The script runs existing unittest discovery, selftest --quick, init and verify using a fresh temporary data/database/uploads/backups directory. It strips inherited PHARM_* settings, clears the AI key, disables printer use, uses a random synthetic owner password, supplies no interactive stdin, and never starts serve or updates the application. No dependency installation, new service or production migration is performed. Temporary test database is removed; synthetic phase logs and report.json remain. Failure/timeout stops subsequent phases and returns nonzero. A passing report explicitly does not accept Windows hardware or production.

Tests verify isolation from inherited live paths/printer/key, fail-fast reporting and timeout cleanup. Real Linux invocation completed: 377 tests passed, zero failures/skips, 33.573 seconds; selftest --quick, synthetic init and verify all exited 0. Python 3.12.14. The three runner safety tests are included in 377. report.json ok=true; windows_hardware_accepted=false.

Report SHA256: b68b5c823865b510dbebc36e27636576c7f78083b3652ed42af85b55a76f138a Windows compatibility is intended, not yet tested. It does not substitute for installer, GUI, power-loss, hardware or owner acceptance. Do not point --output at an existing pharmacy data folder; use a dedicated test-log folder.

See docs/FIRST_RELEASE_SCOPE.md for the finite launch checklist and verified implementation inventory. Code/logic fixes already saved in PR #2–#8 remain separate review work; this main-based branch does not implicitly include them.
