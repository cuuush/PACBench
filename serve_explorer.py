#!/usr/bin/env python3
"""Serve the explorer/ project locally (the ONE command).

Run: /usr/local/bin/python3 serve_explorer.py [port]
Serves explorer/ on 127.0.0.1 and opens the browser. Stdlib only.
"""

import http.server
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8137
BUILD_LOCK = threading.Lock()


class ExplorerHandler(http.server.SimpleHTTPRequestHandler):
    """Refresh the local artifact index whenever the dashboard requests it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "explorer"), **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path == "/data/dashboard.json":
            with BUILD_LOCK:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "build_explorer_data.py")],
                    cwd=ROOT, capture_output=True, text=True, timeout=60)
            if result.returncode:
                body = result.stderr or result.stdout or "artifact index build failed"
                self.send_error(500, body[-1000:])
                return
        super().do_GET()


def main() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), ExplorerHandler)
    url = f"http://127.0.0.1:{PORT}/index.html"
    print(f"serving explorer/ at {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
