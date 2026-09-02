"""Serve build/site/ to `wrangler dev` over HTTP/1.1 with keep-alive.

Python's stock `http.server` speaks HTTP/1.0 and closes every connection, and workerd reuses
connections: the second fetch a request makes ("index.json", then a year page) lands on a
closed socket and fails with "Network connection lost". This is the same server with the
protocol version raised, which is all it takes.

    python site/scripts/serve_payloads.py [--dir build/site] [--port 8788]
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass  # wrangler's log is the one worth reading


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="build/site")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), partial(Handler, directory=args.dir))
    print(
        f"serving {args.dir} on http://{args.bind}:{args.port} (HTTP/1.1, keep-alive)", flush=True
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
