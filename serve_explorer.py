#!/usr/bin/env python3
"""Serve the explorer/ project locally (the ONE command).

Run: /usr/local/bin/python3 serve_explorer.py [port]
Serves explorer/ on 127.0.0.1 and opens the browser. Stdlib only.
"""

import http.server
import json
import re
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

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
        route=urlsplit(self.path)
        if route.path == "/model-call":
            from pacbench.model_calls import public_record
            params=parse_qs(route.query)
            run=params.get('run',[''])[0];name=params.get('file',[''])[0]
            relative=Path(name)
            base=ROOT/'runs'/run
            path=base/relative
            if (not run or Path(run).name!=run or run in ('.','..') or
                    relative.is_absolute() or '..' in relative.parts or
                    not re.fullmatch(r'(?:preflight/)?(?:preparation/|judge-scope-review/|components/[^/]+/(?:judge-scope-review/)?)?model-calls/[^/]+\.json',name) or
                    not path.resolve().is_relative_to(base.resolve()) or
                    any(p.is_symlink() for p in (path,*path.parents) if p!=ROOT)):
                self.send_error(404);return
            try:record=public_record(json.loads(path.read_text(encoding='utf-8')))
            except (OSError,ValueError):self.send_error(404);return
            self.send_json(record);return
        if route.path == "/runs":
            self.send_json(load_runs(ROOT,include_model_records=False));return
        super().do_GET()

    def send_json(self, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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
