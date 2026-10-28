# Windows validation completed — 2026-10-28
Successful run: https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/40745205719
Source commit: 6756d5cab26a169e95b27517b210e924643c4357.
Windows Server 2025 10.0.26100, CPython 3.13.16: 447 discovered, **442 passed, 0 failed, 5 skipped**, 210.172 seconds. selftest --quick, synthetic init and verify also passed.
The separately constructed embedded Windows Python runtime passed selftest --quick, synthetic init and verify. This is actual hosted Windows execution, not the owner's Windows 10 PC or hardware acceptance.
Artifact 12842125929 was downloaded and its ZIP digest verified: e25d0037101af00c143e5fd6d350e3f6036a7a97f6046db42c5dad05f50b07d3. Report saved as WINDOWS_VALIDATION_REPORT.json. Artifact expires 2026-11-04.
First run 40743463128 failed 3 cases (439 passed/5 skipped): two short-versus-long temp path comparisons and a raw backslash ZIP fixture normalized on Windows. Fixed fixture path normalization/raw ZIP generation and raw-name validation; the successful full rerun includes these changes. No skips were added to hide those failures.
Linux baseline composition previously passed 447/447. Restore of a synthetic operating day and resumed sale also passed (RESTORE_ACCEPTANCE.md).
No live pharmacy/patient data, E-Stock, physical printer/scanner/drawer, owner machine, actual installer execution, production deployment or main merge occurred. Full-product future requirements remain separate from this local candidate acceptance.
