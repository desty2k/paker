#!/usr/bin/env python3
"""Remote executor — receives ``{libraries, code}`` frames and runs them.

The client has zero AI libraries installed. It speaks a tiny framed protocol
with the server, imports each bundle with paker (accumulating across calls
via the existing importer), and ``exec``s the code against a persistent
globals dict so state carries from one tool call to the next.

Security property: the Anthropic API key lives only on the server. The client
sees the session's encryption key (derived from the machine ID + shared
secret) and whatever libraries it is asked to run — nothing more.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import socket
import struct
import sys
import traceback
import uuid

import paker

HOST = os.environ.get("PAKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("PAKER_PORT", "9477"))
MASTER_SECRET = os.environ.get("PAKER_SECRET", "demo-secret-change-me")


# ---------------------------------------------------------------------------
# Wire format — same as server
# ---------------------------------------------------------------------------


def send_frame(sock: socket.socket, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">Q", len(data)) + data)


def recv_frame(sock: socket.socket) -> dict:
    header = _recv_exact(sock, 8)
    size = struct.unpack(">Q", header)[0]
    body = _recv_exact(sock, size)
    return json.loads(body)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("connection closed")
        buf.extend(chunk)
    return bytes(buf)


def get_machine_id() -> str:
    return str(uuid.getnode())


def derive_key(machine_id: str) -> bytes:
    return hashlib.sha256(f"{MASTER_SECRET}:{machine_id}".encode()).digest()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


# Anthropic's per-image limit is 5MB base64 (≈3.75MB raw). Give ourselves a
# small buffer so base64 overhead doesn't push us over at the server.
_MAX_IMAGE_BYTES = 4_500_000

_IMAGE_MAGICS = [
    ("image/png", [b"\x89PNG\r\n\x1a\n"]),
    ("image/jpeg", [b"\xff\xd8\xff"]),
    ("image/gif", [b"GIF87a", b"GIF89a"]),
]


def _sniff_media_type(data: bytes) -> str | None:
    for mtype, magics in _IMAGE_MAGICS:
        if any(data.startswith(m) for m in magics):
            return mtype
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def run_fetch_image_frame(frame: dict) -> dict:
    """Read ``frame['path']`` from disk, sniff type, return base64 payload."""
    path = frame["path"]
    call_id = frame["call_id"]
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return {
            "kind": "fetch_result",
            "call_id": call_id,
            "error": f"{type(e).__name__}: {e}",
        }
    if len(data) > _MAX_IMAGE_BYTES:
        return {
            "kind": "fetch_result",
            "call_id": call_id,
            "error": (
                f"file too large ({len(data):,} bytes > "
                f"{_MAX_IMAGE_BYTES:,}); resize or crop first"
            ),
        }
    media_type = _sniff_media_type(data)
    if media_type is None:
        return {
            "kind": "fetch_result",
            "call_id": call_id,
            "error": "unsupported image format (need png/jpeg/gif/webp)",
        }
    return {
        "kind": "fetch_result",
        "call_id": call_id,
        "path": os.path.abspath(path),
        "media_type": media_type,
        "byte_count": len(data),
        "data": base64.b64encode(data).decode("ascii"),
    }


def run_exec_frame(frame: dict, exec_globals: dict, key: bytes) -> dict:
    """Load the bundle (if any) and run the code, capturing stdout/stderr."""
    libraries = frame.get("libraries", [])
    bundle = frame.get("bundle", {})
    code = frame["code"]

    if bundle:
        # ``paker.loads`` reuses the existing JsonImporter on sys.meta_path
        # and appends these modules; repeated calls accumulate libraries.
        print(f"[client] ← exec libs={libraries} bundle={len(bundle)} pkgs")
        paker.loads(bundle, key=key)
    else:
        print(f"[client] ← exec libs={libraries} (all cached)")

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    error = None
    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        compiled = compile(code, "<tool:run_python>", "exec")
        exec(compiled, exec_globals)
    except BaseException:
        error = traceback.format_exc()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return {
        "kind": "result",
        "call_id": frame["call_id"],
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "error": error,
    }


def main() -> None:
    machine_id = get_machine_id()
    key = derive_key(machine_id)

    print(f"[client] Connecting to {HOST}:{PORT} (machine_id={machine_id})...")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((HOST, PORT))
        send_frame(sock, {"kind": "hello", "machine_id": machine_id})

        ready = recv_frame(sock)
        if ready.get("kind") != "ready":
            raise RuntimeError(f"unexpected server frame: {ready!r}")
        print(f"[client] Server at {ready.get('host', '?')}; handshake complete")

        send_frame(sock, {
            "kind": "hello_ack",
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "python": platform.python_version(),
        })

        exec_globals: dict = {"__name__": "__paker_remote__"}

        while True:
            frame = recv_frame(sock)
            kind = frame.get("kind")
            if kind == "bye":
                print("[client] Server closed session")
                return
            if kind == "exec":
                result = run_exec_frame(frame, exec_globals, key)
                send_frame(sock, result)
                continue
            if kind == "fetch_image":
                print(f"[client] ← fetch_image path={frame.get('path')}")
                result = run_fetch_image_frame(frame)
                send_frame(sock, result)
                continue
            print(f"[client] unexpected frame kind={kind}, ignoring")


if __name__ == "__main__":
    main()
