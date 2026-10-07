#!/usr/bin/env python3
"""Serve the explorer/ project locally (the ONE command).

Run: /usr/local/bin/python3 serve_explorer.py [port]
Serves explorer/ on 127.0.0.1 and opens the browser. Stdlib only.
"""

import http.server
import json
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from pacbench.explorer_data import load_runs

ROOT = Path(__file__).resolve().parent


class ExplorerHandler(http.server.SimpleHTTPRequestHandler):
    """Serve public run evidence; private homes are outside the static root."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "explorer"), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if urlsplit(self.path).path == "/runs":
            body = json.dumps(load_runs(ROOT), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8137
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), ExplorerHandler)
    url = f"http://127.0.0.1:{port}/index.html"
    print(f"serving explorer/ at {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
