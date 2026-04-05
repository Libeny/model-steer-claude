#!/usr/bin/env bash
# Hook: session-start — register session with msc proxy and inject CR_SESSION
# NO set -e: must not exit early, CLAUDE_ENV_FILE injection is critical

# Only activate when launched via cr (MSC_ENABLED=1)
[[ "${MSC_ENABLED:-}" != "1" ]] && exit 0

PROXY="http://127.0.0.1:3457"

# Read all stdin first (Claude Code pipes JSON)
STDIN_DATA=$(cat 2>/dev/null || true)

# Extract session_id from stdin JSON
SESSION_ID=""
if [ -n "$STDIN_DATA" ]; then
  SESSION_ID=$(echo "$STDIN_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || true)
fi

if [[ -z "$SESSION_ID" ]]; then
  exit 0
fi

# Inject CR_SESSION into the Claude env file so subsequent hooks/tools can use it
if [[ -n "${CLAUDE_ENV_FILE:-}" ]]; then
  echo "export CR_SESSION=$SESSION_ID" >> "$CLAUDE_ENV_FILE"
fi

# Register session with msc proxy (fire-and-forget, 0.5s timeout)
curl --noproxy '*' -s -m 0.5 "${PROXY}/register?session=${SESSION_ID}" >/dev/null 2>&1 || true

# Query status and display
STATUS=$(curl --noproxy '*' -s -m 0.5 "${PROXY}/status?session=${SESSION_ID}" 2>/dev/null || true)
if [[ -n "$STATUS" ]]; then
  LEVEL=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('level',1))" 2>/dev/null || echo "1")
  NAME=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name','glm'))" 2>/dev/null || echo "glm")
  echo "[msc] Session: ${SESSION_ID:0:8} | Level ${LEVEL} (${NAME}) | /think-level 1|2|3 | /smoke | /redbull"
else
  echo "[msc] Session: ${SESSION_ID:0:8} | Level 1 (glm) | /think-level 1|2|3 | /smoke | /redbull"
fi
