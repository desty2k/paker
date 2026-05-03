#!/usr/bin/env bash
# Orchestrate the IP-protection demo end-to-end.
#
# Sequence:
#   1. pack.py       — produce bundle.paker + release.key (dev role)
#   2. license_server.py — start background HTTP key server
#   3. client.py     — fetch key, load bundle, run signal (customer role)
#   4. attack.py     — what a bundle-only attacker sees (attacker role)
#
# Run once; it's idempotent. The license server is stopped on exit.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

cd "$HERE"

echo ">>> [1/4] pack.py — vendor packs the proprietary algorithm"
"$PY" pack.py
echo

echo ">>> [2/4] license_server.py — starting key server in background"
"$PY" license_server.py >license.log 2>&1 &
LICENSE_PID=$!
trap 'kill "$LICENSE_PID" 2>/dev/null || true' EXIT
# Wait for the server to accept connections.
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:9478/key \
        -H 'Content-Type: application/json' -d '{"machine_id":"ping"}' >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done
cat license.log
echo

echo ">>> [3/4] client.py — customer fetches the key and runs the signal"
"$PY" client.py
echo

echo ">>> [4/4] attack.py — what a bundle-only attacker sees"
"$PY" attack.py
echo

echo ">>> demo complete"
