"""Shared runner for sale-screen browser tests: serves the test app and runs a Playwright script in node."""
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path


def run_node(case, script, *args, behind=None):
    """Runs `script` against case.app; returns the JSON object printed on its last line.
    `behind(flags_dir)` (optional) runs in a thread next to the browser, for changes made behind the screen."""
    import uvicorn
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(case.app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    w = threading.Thread(target=behind, daemon=True) if behind else None
    if w:
        w.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        r = subprocess.run([shutil.which("node"), "-e", script, f"http://127.0.0.1:{port}", *args],
                           capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).parent))
    finally:
        server.should_exit = True
        t.join(10)
        if w:
            w.join(10)
    case.assertEqual(r.returncode, 0, r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


LOGIN = r"""
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
"""
