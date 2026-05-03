#!/usr/bin/env python3
"""Agent server — holds the Claude API key and drives a remote executor.

Architecture: the agent loop (LLM calls, tool dispatch) runs **on the server**.
The API key never leaves this process. When Claude asks to run code, the server
packs the required libraries with paker, encrypts them with the client's
session key, and ships ``{libraries, code}`` to the client. The client imports
the bundle from memory, runs the code, returns stdout/stderr.

Security property: compromising the client yields the per-session encryption
key and whatever libraries have shipped so far, but never the API key or the
server's bundle of source.

Wire protocol (length-prefixed JSON frames):
* ``hello {machine_id}`` — client → server, once.
* ``exec {libraries, bundle, code, call_id}`` — server → client.
* ``result {call_id, stdout, stderr, error}`` — client → server.

Both directions are 8-byte big-endian length + JSON bytes.
"""

from __future__ import annotations

import getpass
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import socket
import struct

import paker

HOST = os.environ.get("PAKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("PAKER_PORT", "9477"))
MASTER_SECRET = os.environ.get("PAKER_SECRET", "demo-secret-change-me")
MODEL = os.environ.get("PAKER_MODEL", "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# Wire format
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


# ---------------------------------------------------------------------------
# Key material + bundle cache
# ---------------------------------------------------------------------------


def derive_key(machine_id: str) -> bytes:
    return hashlib.sha256(f"{MASTER_SECRET}:{machine_id}".encode()).digest()


def get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        print("[server] Using API key from ANTHROPIC_API_KEY env var")
        return key
    return getpass.getpass("[server] Enter Anthropic API key: ")


def resolve_import_name(name: str) -> str:
    """Map a PyPI distribution name to the top-level import name.

    ``Pillow``→``PIL``, ``PyYAML``→``yaml``, ``scikit-learn``→``sklearn``,
    ``opencv-python``→``cv2``. Falls back to the input when the name is
    already importable or no mapping is available — giving ``paker.dumps``
    a name it can actually ``import_module``.
    """
    # Fast path: name is importable as-is.
    try:
        importlib.import_module(name)
        return name
    except ImportError:
        pass

    # Distribution metadata carries ``top_level.txt`` or ``Provides``
    # headers that list the real import name. ``packages_distributions``
    # is the inverse map (import→distributions); we walk it.
    try:
        dist_to_imports = {
            imp: dist
            for imp, dists in importlib.metadata.packages_distributions().items()
            for dist in dists
        }
    except Exception:
        dist_to_imports = {}
    # Case-insensitive, normalised comparison (PEP 503).
    normalised = name.lower().replace("-", "_")
    for imp_name, dist_name in dist_to_imports.items():
        if dist_name.lower().replace("-", "_") == normalised:
            return imp_name

    # Last resort: return the original; ``paker.dumps`` will raise a
    # descriptive error we can surface to the model.
    return name


class PackError(Exception):
    """Raised when ``BundleCache.pack`` can't fulfil a libraries request."""


class BundleCache:
    """Packs library sets on demand, caches by frozenset of library names.

    Avoids re-packing ``psutil`` on every tool call that asks for it. The
    cache is per-session because the encryption key is per-client.
    """

    def __init__(self, key: bytes):
        self._key = key
        self._cache: dict[frozenset, dict] = {}
        self._already_sent: set[str] = set()

    def pack(self, libraries: list[str]) -> dict:
        """Return a bundle containing ``libraries`` + their deps.

        Each name is normalised from distribution form (``Pillow``) to
        import form (``PIL``). Failures raise :class:`PackError` so the
        caller can surface a message to the model instead of shipping an
        empty bundle and leaving the client with ``ModuleNotFoundError``.
        Only modules the client hasn't received yet are returned.
        """
        resolved = {resolve_import_name(lib) for lib in libraries}
        resolved.discard("")
        wanted = frozenset(resolved)
        if not wanted:
            return {}
        if wanted in self._cache:
            bundle = self._cache[wanted]
        else:
            bundle = {}
            failures: list[str] = []
            for lib in sorted(wanted):
                try:
                    importlib.import_module(lib)
                except ImportError as e:
                    failures.append(f"{lib}: not importable in server venv ({e})")
                    continue
                try:
                    piece = paker.dumps(lib, key=self._key, include_deps=True)
                except Exception as e:
                    failures.append(f"{lib}: {type(e).__name__}: {e}")
                    continue
                if not piece:
                    failures.append(f"{lib}: paker returned empty bundle")
                    continue
                bundle.update(piece)
            if failures:
                # Surface the exact failure(s) to the agent loop so the
                # model gets a useful error instead of a silent empty
                # bundle + ``ModuleNotFoundError`` on the client.
                raise PackError("; ".join(failures))
            self._cache[wanted] = bundle

        # Only ship what the client hasn't seen yet.
        trimmed = {
            name: node for name, node in bundle.items()
            if name not in self._already_sent
        }
        self._already_sent.update(trimmed)
        return trimmed


# ---------------------------------------------------------------------------
# Remote executor — shipped to Claude as a tool
# ---------------------------------------------------------------------------


class RemoteExecutor:
    """Bridges Claude's tool calls to the client over the open socket."""

    def __init__(self, sock: socket.socket, cache: BundleCache):
        self._sock = sock
        self._cache = cache
        self._call_id = 0

    def fetch_image(self, path: str) -> dict:
        """Ask the client to read a file from its filesystem and b64-encode it.

        Returns a dict ready for consumption by :func:`build_tool_result`;
        either a rich image block or an error string.
        """
        self._call_id += 1
        print(f"[server] → fetch_image call_id={self._call_id} path={path}")
        send_frame(self._sock, {
            "kind": "fetch_image",
            "call_id": self._call_id,
            "path": path,
        })
        reply = recv_frame(self._sock)
        if reply.get("kind") != "fetch_result" or reply.get("call_id") != self._call_id:
            raise RuntimeError(f"unexpected reply: {reply!r}")
        return reply

    def run(self, libraries: list[str], code: str) -> dict:
        self._call_id += 1
        try:
            bundle = self._cache.pack(libraries)
        except PackError as e:
            # Surface as a synthetic result frame so the agent loop treats
            # it like any other tool error and Claude gets actionable text.
            print(
                f"[server] → exec call_id={self._call_id} "
                f"libs={libraries} PACK-FAIL: {e}",
            )
            return {
                "kind": "result",
                "call_id": self._call_id,
                "stdout": "",
                "stderr": "",
                "error": (
                    f"paker pack failed for {libraries}: {e}. "
                    f"Report this to the operator and STOP: the operator "
                    f"needs to ``pip install`` the missing library in the "
                    f"server venv before the request can be retried. Do "
                    f"not pip install on the client; do not retry with a "
                    f"different name."
                ),
            }
        print(
            f"[server] → exec call_id={self._call_id} "
            f"libs={libraries} ship={len(bundle)} bundled",
        )
        send_frame(self._sock, {
            "kind": "exec",
            "call_id": self._call_id,
            "libraries": libraries,
            "bundle": bundle,
            "code": code,
        })
        reply = recv_frame(self._sock)
        if reply.get("kind") != "result" or reply.get("call_id") != self._call_id:
            raise RuntimeError(f"unexpected reply: {reply!r}")
        return reply


# ---------------------------------------------------------------------------
# Tool schema + dispatch
# ---------------------------------------------------------------------------


TOOLS = [
    {
        "name": "run_python",
        "description": (
            "Execute Python code on the remote client. The client has only "
            "paker installed; declare every third-party library the code "
            "imports in ``libraries``, and the server will pack + ship them "
            "on demand. State (module imports, variables) persists between "
            "calls. Use ``print()`` for output; the return value is ignored."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "libraries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Third-party packages the code imports, given as "
                        "their IMPORT names (e.g. ['PIL', 'yaml', 'cv2', "
                        "'sklearn']), NOT their PyPI distribution names "
                        "('Pillow', 'PyYAML', 'opencv-python', "
                        "'scikit-learn'). Stdlib modules do not need to "
                        "be listed. Empty list is fine for stdlib-only code."
                    ),
                },
                "code": {
                    "type": "string",
                    "description": "Python code to run on the remote host.",
                },
            },
            "required": ["libraries", "code"],
        },
    },
    {
        "name": "fetch_image",
        "description": (
            "Read a PNG/JPEG/GIF/WEBP file from the remote host and attach "
            "it to your context so you can see it. Use this after saving a "
            "screenshot (PIL.ImageGrab → img.save(path)) or any diagnostic "
            "image. The path is a plain filesystem path on the remote. "
            "Max ~4MB raw; call a second time with a smaller crop/resize "
            "if the file exceeds it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Filesystem path on the remote (absolute or "
                        "relative to the client's cwd)."
                    ),
                },
            },
            "required": ["path"],
        },
    },
]


# Per-stream cap for a single tool call's output going into Claude's
# conversation. Anything above this is replaced with a truncation marker.
# Claude almost never needs more than this to reason; accumulating full
# outputs (ps auxf, find /, huge file dumps) blows the session context
# window in a few calls.
_MAX_STREAM_CHARS = 50_000


def _truncate(s: str, label: str) -> str:
    if len(s) <= _MAX_STREAM_CHARS:
        return s
    head = s[: _MAX_STREAM_CHARS // 2]
    tail = s[-_MAX_STREAM_CHARS // 2 :]
    dropped = len(s) - len(head) - len(tail)
    return f"{head}\n... [paker truncated {dropped:,} chars of {label}] ...\n{tail}"


def build_fetch_image_result(tool_use_id: str, reply: dict) -> dict:
    """Turn the client's fetch_result frame into a Claude tool_result block.

    On success the block carries an ``image`` content block plus a short
    text acknowledgement so the model can reason about the image by
    filename. On failure the block is plain text with the error.
    """
    if reply.get("error"):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"[error] fetch_image: {reply['error']}",
            "is_error": True,
        }
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": reply["media_type"],
                    "data": reply["data"],
                },
            },
            {
                "type": "text",
                "text": (
                    f"Attached {reply['path']} "
                    f"({reply['media_type']}, {reply['byte_count']:,} bytes)."
                ),
            },
        ],
    }


def format_result(result: dict) -> str:
    """Render the executor's reply into a single string for Claude."""
    parts = []
    if result.get("stdout"):
        parts.append(_truncate(result["stdout"].rstrip(), "stdout"))
    if result.get("stderr"):
        parts.append(f"[stderr]\n{_truncate(result['stderr'].rstrip(), 'stderr')}")
    if result.get("error"):
        parts.append(f"[error] {_truncate(result['error'], 'error')}")
    return "\n".join(parts) if parts else "(no output)"


# ---------------------------------------------------------------------------
# Agent loop (Claude conversation)
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "Remote admin agent. The operator at the server prompt owns the "
    "machine and has delegated full access — don't refuse routine tasks "
    "(install/uninstall packages, read configs, inspect processes, read "
    "clipboard, screenshots, edit files, restart services). Confirm once "
    "before genuinely destructive actions (rm -rf user data, shutdown, "
    "wiping credentials); otherwise just do it.\n\n"
    "run_python is a full CPython interpreter on the remote. subprocess, "
    "os, shutil, pathlib, socket all work. No separate shell tool — use "
    "subprocess.run. No pip binary on PATH: always invoke as "
    "``[sys.executable, '-m', 'pip', ...]``.\n\n"
    "Critical: paker-shipped libraries live only in THIS Python process. "
    "Spawning a child Python (subprocess.Popen([sys.executable, '-c', "
    "...]) or similar) produces a fresh interpreter that cannot see any "
    "of them — the child will ModuleNotFoundError on every paker-shipped "
    "import. To run background services (HTTP servers, workers), keep "
    "them in-process: launch in a ``threading.Thread(daemon=True)`` or "
    "via the library's async API (e.g. ``uvicorn.Server(config).serve()`` "
    "inside ``asyncio.run`` in a thread). The exec globals persist, so "
    "keep a reference to the server/thread so later tool calls can stop "
    "or inspect it.\n\n"
    "Third-party libraries: declare them in the tool's ``libraries`` "
    "parameter and paker ships from the server venv. Use IMPORT names "
    "(PIL, yaml, cv2, sklearn), not PyPI names (Pillow, PyYAML, "
    "opencv-python, scikit-learn). If pack fails, STOP and tell the "
    "operator which library needs ``pip install`` on the server venv, "
    "then wait. Do not pip install it on the client, do not retry with "
    "a different library name hoping it works, do not improvise a "
    "stdlib substitute. The operator will install the missing library "
    "and reissue the request. This applies equally when the original "
    "tool call failed on a transitively-missing optional dep (jinja2, "
    "email-validator, python-multipart, etc.).\n\n"
    "State persists across tool calls and prompts in this session, and "
    "dies with the server.\n\n"
    "Response style: terse. No preambles ('Here's the summary:'), no "
    "trailing offers ('Let me know if...'), no markdown tables for two "
    "rows. Report the answer in one or two short lines and stop. Tool "
    "output is kept in context in full — prefer summaries, slices, and "
    "counts over dumping whole files or process lists. Save binaries "
    "(screenshots, PDFs) to a path and report the path; never print them."
)


class AgentSession:
    """One live Claude conversation that outlives individual prompts.

    ``messages`` accumulates every turn of the TCP session: user prompts,
    assistant turns (including tool_use blocks), and tool_result blocks.
    Dropped when the server process exits — no persistence on either side.
    """

    def __init__(self, anthropic_mod, api_key: str, executor: "RemoteExecutor"):
        self._client = anthropic_mod.Anthropic(api_key=api_key)
        self._executor = executor
        self._messages: list[dict] = []

    def ask(self, prompt: str) -> str:
        self._messages.append({"role": "user", "content": prompt})
        transcript: list[str] = []

        while True:
            approx_chars = sum(
                len(json.dumps(m, default=str)) for m in self._messages
            )
            print(
                f"[server] messages={len(self._messages)} "
                f"approx={approx_chars:,} chars (~{approx_chars // 4:,} tokens)",
            )
            response = self._client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self._messages,
            )

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        transcript.append(block.text)
                self._messages.append(
                    {"role": "assistant", "content": response.content},
                )
                break

            if response.stop_reason != "tool_use":
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        transcript.append(block.text)
                self._messages.append(
                    {"role": "assistant", "content": response.content},
                )
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "run_python":
                    libs = block.input.get("libraries", [])
                    code = block.input["code"]
                    transcript.append(
                        f"[tool:{block.name}] libs={libs}\n{code}"
                    )
                    result = self._executor.run(libs, code)
                    rendered = format_result(result)
                    transcript.append(f"[result]\n{rendered}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": rendered,
                    })
                elif block.name == "fetch_image":
                    path = block.input["path"]
                    transcript.append(f"[tool:{block.name}] path={path}")
                    reply = self._executor.fetch_image(path)
                    tool_results.append(build_fetch_image_result(block.id, reply))
                    if reply.get("error"):
                        transcript.append(f"[result]\n[error] {reply['error']}")
                    else:
                        transcript.append(
                            f"[result] image attached "
                            f"({reply['media_type']}, {reply['byte_count']:,} bytes)",
                        )
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"[error] unknown tool: {block.name}",
                    })

            self._messages.append(
                {"role": "assistant", "content": response.content},
            )
            self._messages.append({"role": "user", "content": tool_results})

        return "\n\n".join(transcript)

    def reset(self) -> None:
        self._messages.clear()


# ---------------------------------------------------------------------------
# Connection handler
# ---------------------------------------------------------------------------


def handle_client(conn: socket.socket, addr, api_key: str) -> None:
    print(f"[server] Client connected from {addr}")
    try:
        hello = recv_frame(conn)
        if hello.get("kind") != "hello":
            print(f"[server] unexpected handshake: {hello!r}")
            return
        machine_id = hello["machine_id"]
        print(f"[server] Machine ID: {machine_id}")

        key = derive_key(machine_id)
        cache = BundleCache(key)
        executor = RemoteExecutor(conn, cache)

        # Ship paker's key up-front — everything after this is a session key
        # derived from machine_id + shared secret.
        send_frame(conn, {"kind": "ready", "host": platform.node()})
        client_ready = recv_frame(conn)
        if client_ready.get("kind") != "hello_ack":
            print(f"[server] client refused handshake: {client_ready!r}")
            return
        print(f"[server] Client ready on {client_ready.get('platform', '?')}")

        import anthropic

        session = AgentSession(anthropic, api_key, executor)

        print("[server] Type prompts (Ctrl+D / Ctrl+C to exit; /reset to clear history):")
        print()
        while True:
            try:
                prompt = input("> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not prompt.strip():
                continue
            if prompt.strip() == "/reset":
                session.reset()
                print("[server] conversation history cleared")
                continue

            try:
                reply = session.ask(prompt)
            except Exception as e:
                print(f"[server] agent error: {type(e).__name__}: {e}")
                continue
            print()
            for line in reply.splitlines() or [""]:
                print(f"  │ {line}")
            print()

        send_frame(conn, {"kind": "bye"})
    except (ConnectionError, BrokenPipeError) as e:
        print(f"[server] Connection lost: {e}")
    finally:
        conn.close()


def main() -> None:
    api_key = get_api_key()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, PORT))
        srv.listen(1)
        print(f"[server] Listening on {HOST}:{PORT}")

        conn, addr = srv.accept()
        handle_client(conn, addr, api_key)


if __name__ == "__main__":
    main()
