# Windows candidate validation
First run: https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/40743463128
Commit 83d3e48b374b9c79836a53b63b06137256e82b7c, Windows Server 2025 10.0.26100, Python 3.13.16.
447 discovered: 439 passed, 3 failures, 5 skipped (158.442 seconds). Validator correctly stopped before selftest/init/verify.
Two failures compared short Windows temp-directory names against resolved long names for the same folder. Normalize the test fixture base with resolve(), preserving assertions.
The third test's ZIP fixture normalized backslashes during ZipInfo creation on Windows. Preserve deliberate raw invalid member bytes in the fixture and inspect orig_filename before staging, since Windows ZipInfo may already normalize filename. No unsafe archive was installed or activated; no production incident is claimed.
Local targeted check after these bounded changes: 24 tests passed. A new complete Windows run is required; do not treat this as Windows success.
Prior synthetic restore drill passed on Linux. This hosted Windows server is not the owner's Windows 10 machine, embedded installer or physical printer/hardware acceptance.
