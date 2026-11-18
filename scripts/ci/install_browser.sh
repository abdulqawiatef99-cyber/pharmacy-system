#!/usr/bin/env bash
# Install Chromium for the UI tests (CI, linux). `--with-deps` needs the Ubuntu apt mirrors; when they are
# unreachable (11/11: every mirror timed out, apt exit 100, every PR blocked) install the browser alone:
# the runner image already carries Chromium's libraries, and PHARM_REQUIRE_BROWSER still fails the job if
# the browser cannot start. The fallback's exit status is the script's exit status.
set -u
PW="${1:?usage: install_browser.sh <path to the playwright cli>}"
LIMIT="${PW_DEPS_TIMEOUT:-240}"
if timeout "$LIMIT" "$PW" install --with-deps chromium; then
  exit 0
fi
echo "::warning::apt mirrors unreachable; installing Chromium without system packages"
"$PW" install chromium
