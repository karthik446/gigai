"""Test-only server of the demo gig: stdlib HTTP on 127.0.0.1:<port> and one grandchild process.

`GET /health` answers 200 `ok`; `GET /` answers `demo <version>` (the installed gig's version, so a restart onto build B
shows). At start the server launches one sleeping grandchild whose command line names `gig-demo-grandchild` and the data
folder; it stays in the server's process group, so a test can check that stopping the group ends it too. Failure modes, from
the environment: `GIG_DEMO_IGNORE_TERM=1` makes the server (and its grandchild) ignore SIGTERM, so only SIGKILL ends them;
`GIG_DEMO_NEVER_HEALTHY=1` makes `/health` answer 503; `GIG_DEMO_EXIT_CODE=<n>` exits with <n> right after the grandchild starts.
"""

import argparse
import os
import signal
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata

GRANDCHILD_MARKER = "gig-demo-grandchild"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health" and os.environ.get("GIG_DEMO_NEVER_HEALTHY") == "1":
            self.send_error(503)
            return
        if self.path == "/health":
            body = b"ok\n"
        elif self.path == "/":
            body = f"demo {metadata.version('gigai-test-gig-demo')}\n".encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    if os.environ.get("GIG_DEMO_IGNORE_TERM") == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", GRANDCHILD_MARKER, args.data])
    if os.environ.get("GIG_DEMO_EXIT_CODE"):
        sys.exit(int(os.environ["GIG_DEMO_EXIT_CODE"]))
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
