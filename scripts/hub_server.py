#!/usr/bin/env python3
"""
scripts/hub_server.py — the demo hub's static server.

Serves hub/ (a landing page that lists the demos running on this box and links to
each one) on a local port. A Cloudflare Tunnel ingress rule points the root
hostname at it; the demos themselves are separate ingress rules / hostnames.

    python3 scripts/hub_server.py            # port 8080 (HUB_PORT)

Stdlib only — no venv needed, so it can run as its own tiny systemd unit that has
no dependency on either demo's environment.

Edit hub/demos.json to add or relabel demos; no code change or restart required
(the page fetches it on load).
"""
from __future__ import annotations

import functools
import http.server
import os
import socketserver
from pathlib import Path

HUB_DIR = Path(os.getenv("HUB_DIR", Path(__file__).resolve().parent.parent / "hub"))
PORT = int(os.getenv("HUB_PORT", "8080"))
HOST = os.getenv("HUB_HOST", "127.0.0.1")  # the tunnel connects locally


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # demos.json is edited live; never let a proxy or browser pin a stale copy.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter logs for a systemd journal
        if not self.path.startswith("/favicon"):
            super().log_message(fmt, *args)


def main() -> int:
    if not (HUB_DIR / "index.html").is_file():
        print(f"✗ no index.html in {HUB_DIR}")
        return 1
    handler = functools.partial(Handler, directory=str(HUB_DIR))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, PORT), handler) as httpd:
        print(f"hub serving {HUB_DIR} on http://{HOST}:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nhub stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
