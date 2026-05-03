#!/usr/bin/env bash
# Create the two venvs this demo expects: a server venv with anthropic +
# the libraries the agent is allowed to ship, and a client venv with only
# paker. Run once; re-running is idempotent.

set -euo pipefail

PAKER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER_VENV="${SERVER_VENV:-/tmp/paker-server-venv}"
CLIENT_VENV="${CLIENT_VENV:-/tmp/paker-client-venv}"
PYTHON="${PYTHON:-python3}"

echo "[setup] Server venv: $SERVER_VENV"
"$PYTHON" -m venv "$SERVER_VENV"
"$SERVER_VENV/bin/pip" install --quiet --upgrade pip
"$SERVER_VENV/bin/pip" install --quiet -e "$PAKER_ROOT" anthropic psutil

echo "[setup] Client venv: $CLIENT_VENV"
"$PYTHON" -m venv "$CLIENT_VENV"
"$CLIENT_VENV/bin/pip" install --quiet --upgrade pip
"$CLIENT_VENV/bin/pip" install --quiet -e "$PAKER_ROOT"

echo
echo "[setup] Server venv packages:"
"$SERVER_VENV/bin/pip" list | grep -iE "paker|anthropic|psutil|httpx|pydantic" || true
echo
echo "[setup] Client venv packages:"
"$CLIENT_VENV/bin/pip" list | grep -iE "paker|anthropic|psutil|httpx|pydantic" || true
echo
echo "[setup] Done. Run the server:"
echo "  ANTHROPIC_API_KEY=sk-ant-... $SERVER_VENV/bin/python $(dirname "${BASH_SOURCE[0]}")/server.py"
echo "[setup] And the client:"
echo "  $CLIENT_VENV/bin/python $(dirname "${BASH_SOURCE[0]}")/client.py"
