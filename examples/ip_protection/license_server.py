"""Mock license server: hands out the per-session decryption key to
authenticated clients.

In a real deployment this would check a license record, revocation list,
rate limit, and ideally bind the key to a device attestation. The demo
just verifies the posted ``machine_id`` is on a hardcoded allowlist.

Key property for paker's threat model: the master key never leaves this
process in any form except the HTTP response body, and only to licensed
callers. A captured ``bundle.paker`` on a customer's disk is unrunnable
without a live call to this endpoint.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
from socketserver import TCPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("LICENSE_PORT", "9478"))
# Hardcoded allowlist for the demo. Replace with a real DB in production.
LICENSED_MACHINES = {"demo-machine-001", "demo-machine-002"}

_master_key: bytes | None = None


def _load_master_key() -> bytes:
    path = os.path.join(HERE, "release.key")
    if not os.path.exists(path):
        print("[license] release.key not found — run pack.py first.", file=sys.stderr)
        sys.exit(1)
    with open(path, "rb") as f:
        return f.read()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003 — stdlib signature
        print(f"[license] {self.address_string()} - {fmt % args}")

    def do_POST(self) -> None:
        if self.path != "/key":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "invalid json")
            return
        machine_id = payload.get("machine_id")
        if machine_id not in LICENSED_MACHINES:
            print(f"[license] DENIED machine_id={machine_id!r}")
            self.send_error(403, "not licensed")
            return
        print(f"[license] GRANTED machine_id={machine_id!r}")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(_master_key)))
        self.end_headers()
        self.wfile.write(_master_key)


def main() -> None:
    global _master_key
    _master_key = _load_master_key()
    with TCPServer(("127.0.0.1", PORT), Handler) as server:
        print(f"[license] listening on http://127.0.0.1:{PORT}/key")
        print(f"[license] licensed machine_ids: {sorted(LICENSED_MACHINES)}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()
            print("[license] stopped")


if __name__ == "__main__":
    main()
