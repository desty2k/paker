# Remote Agent Demo

An AI agent driven by Claude runs on a trusted **server** and executes code
on a zero-install **client** over TCP. Each tool call ships the libraries the
code needs, encrypted with a per-session key, and the client imports them
from memory with paker.

## Threat model

The previous version of this demo shipped the API key to the client — a
client compromise leaked the key. The current architecture keeps the API
key on the server:

| Runs on server | Runs on client |
|---|---|
| Claude API key | Per-session encryption key |
| Paker packer (access to full library set) | Paker loader only |
| Agent loop (LLM calls, tool dispatch) | Plain `exec()` of delivered code |

A compromised client yields whichever libraries have already been shipped
plus the session encryption key; it never sees the Anthropic API key, the
model conversation history from other sessions, or the bundle of source.

## Architecture

```
┌────────────────────────────┐      TCP       ┌────────────────────────────┐
│          SERVER            │ ─────────────> │           CLIENT           │
│                            │                │                            │
│  anthropic + paker         │  { bundle,     │  paker only                │
│  ANTHROPIC_API_KEY         │    code,       │                            │
│  conversation state        │    libraries } │  exec globals (persistent) │
│                            │ ─────────────> │                            │
│  agent_loop() per prompt:  │                │  paker.loads(bundle, key)  │
│   1. messages.create(...)  │                │  exec(code, globals)       │
│   2. for each tool_use:    │                │                            │
│      pack libs + encrypt   │  { stdout,     │  captures stdout/stderr    │
│      send(exec frame)      │    stderr,     │                            │
│      recv(result frame)    │    error }     │                            │
│      feed back to Claude   │ <───────────── │                            │
│   3. continue until        │                │                            │
│      end_turn              │                │                            │
└────────────────────────────┘                └────────────────────────────┘
```

## Tool contract

Claude is given a single `run_python` tool:

```json
{
  "libraries": ["psutil"],
  "code": "import psutil; print(psutil.cpu_count(), psutil.virtual_memory().percent)"
}
```

`libraries` is the list of third-party packages the code imports. Stdlib
doesn't need to be declared. The server packs each listed library (with
transitive dependencies) via `paker.dumps(...)` and ships the encrypted
bundle with the code. Bundles are cached by `frozenset(libraries)` so
repeated tool calls don't re-pack, and each client only receives a package
the first time it's referenced.

## Wire protocol

Length-prefixed JSON frames: `struct.pack(">Q", len(body))` + body. Frames:

| Direction | kind | fields |
|---|---|---|
| client → server | `hello` | `machine_id` |
| server → client | `ready` | `host` |
| client → server | `hello_ack` | `platform`, `python` |
| server → client | `exec` | `call_id`, `libraries`, `bundle`, `code` |
| client → server | `result` | `call_id`, `stdout`, `stderr`, `error` |
| server → client | `bye` | — |

## Session encryption

```
session_key = SHA-256(PAKER_SECRET + ":" + client_machine_id)
```

Both sides derive it independently. The server uses it for `paker.dumps(..., key=...)`;
the client uses it for `paker.loads(..., key=...)`. A bundle captured from
the wire cannot be replayed on a different machine.

## Prerequisites — separate virtual environments

The whole point of the split is that the client runs in a tiny venv with
only paker installed; the server venv holds the real libraries and the
API key. Keep them apart.

One-shot setup script (creates `/tmp/paker-{server,client}-venv`):
```bash
./setup_venvs.sh
```

Or manually:
```bash
# Server venv — paker + anthropic + whatever the agent should be able to
# pack and ship. Add libraries here to make them available as tools.
python -m venv /tmp/paker-server-venv
/tmp/paker-server-venv/bin/pip install paker anthropic psutil
# optional: httpx, requests, numpy, Pillow, ...

# Client venv — paker only. No AI libraries, no psutil, nothing extra.
python -m venv /tmp/paker-client-venv
/tmp/paker-client-venv/bin/pip install paker
```

Want to give the agent a new capability? Install the library in the
**server** venv only — the client will receive it the first time Claude
asks for it:
```bash
/tmp/paker-server-venv/bin/pip install numpy
```

## Running

**Terminal 1 — server:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
/tmp/paker-server-venv/bin/python server.py
```

**Terminal 2 — client:**
```bash
/tmp/paker-client-venv/bin/python client.py
```

Prompts in the server terminal:

```
> what's the CPU and memory load on the remote?
[tool:run_python] libs=['psutil']
import psutil
print(f"CPU: {psutil.cpu_percent(interval=1)}%")
print(f"RAM: {psutil.virtual_memory().percent}%")
[result]
CPU: 4.2%
RAM: 62.1%

> list the five largest files under /tmp
[tool:run_python] libs=[]
import os, glob
paths = glob.glob('/tmp/**', recursive=True)
sized = [(p, os.path.getsize(p)) for p in paths if os.path.isfile(p)]
sized.sort(key=lambda x: -x[1])
for p, s in sized[:5]:
    print(f"{s:>12}  {p}")
[result]
   48210421  /tmp/paker-pillow-screengrab.png
    ...
```

## Configuration

| Environment variable | Default | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | (prompt) | server |
| `PAKER_SECRET` | `demo-secret-change-me` | both — must match |
| `PAKER_HOST` | `127.0.0.1` | both |
| `PAKER_PORT` | `9477` | both |
| `PAKER_MODEL` | `claude-sonnet-4-6` | server |

## State on the server

One TCP connection = one Claude conversation. Every prompt you type at
the `> ` prompt appends to the same `messages` list, so Claude sees the
full back-and-forth (including earlier tool calls and their output) on
every new turn. Close the server and the conversation is gone — there's
no on-disk history. Type `/reset` at the `> ` prompt to clear the history
mid-session without reconnecting.

## State on the client

The client keeps a single `exec_globals` dict for the whole session, so
imports and variables persist between tool calls:

```
> import psutil and bind it to a variable
[tool:run_python] libs=['psutil']
import psutil
p = psutil.Process()
print(p.pid)
[result]
52341

> what was p.pid again?
[tool:run_python] libs=[]
print(p.pid)   # same process object, same globals dict
[result]
52341
```

The first call ships `psutil`; the second needs no bundle because the
library is already loaded into the client's importer.

## Limitations

- **Single client.** The server accepts one TCP connection and drives one
  Claude conversation. Multiple clients or multi-turn sessions would
  require a session table keyed by machine ID.
- **Plaintext tool output.** `stdout` / `stderr` travel back over the
  socket unencrypted. If the channel is untrusted, wrap in TLS.
- **Untrusted client.** The client runs arbitrary code the server sends
  it. That's the whole point of the demo, but it means the server must
  trust itself — don't expose the server terminal to an untrusted user.
