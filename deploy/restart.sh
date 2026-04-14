#!/bin/bash
# Bounce the wiki LaunchAgent and verify it came back healthy.
#
#   ./deploy/restart.sh
#
# Use this whenever you change app/* or static/* — launchd doesn't pick up
# code changes on its own and there's no --reload in the prod plist.

set -euo pipefail

LABEL="com.harshdeep.wiki"
PORT="${PORT:-8765}"
TARGET="gui/$(id -u)/${LABEL}"

if ! launchctl print "${TARGET}" >/dev/null 2>&1; then
  echo "✗ ${LABEL} is not loaded into launchd." >&2
  echo "  load it first:" >&2
  echo "    cp deploy/com.harshdeep.wiki.plist ~/Library/LaunchAgents/" >&2
  echo "    launchctl load -w ~/Library/LaunchAgents/com.harshdeep.wiki.plist" >&2
  exit 1
fi

echo "▸ kickstart ${LABEL}"
launchctl kickstart -k "${TARGET}"

echo "▸ waiting for healthcheck on http://127.0.0.1:${PORT}/healthz"
for _ in $(seq 1 25); do
  sleep 0.2
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    pid=$(launchctl list | awk -v lbl="${LABEL}" '$3==lbl {print $1}')
    body=$(curl -sS "http://127.0.0.1:${PORT}/healthz")
    echo "✓ healthy  pid=${pid}  ${body}"
    exit 0
  fi
done

echo "✗ healthcheck failed after restart" >&2
echo "--- last 30 lines of ~/Library/Logs/wiki.err.log ---" >&2
tail -30 ~/Library/Logs/wiki.err.log >&2 || true
exit 1
