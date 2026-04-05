#!/usr/bin/env bash
# Hook: user-prompt-submit — show current msc level before each prompt
set -euo pipefail

# Only activate when launched via cr (MSC_ENABLED=1)
[[ "${MSC_ENABLED:-}" != "1" ]] && exit 0

if [[ -z "${CR_SESSION:-}" ]]; then
  exit 0
fi

PROXY="http://127.0.0.1:3457"

STATUS=$(curl --noproxy '*' -s -m 0.5 "${PROXY}/status?session=${CR_SESSION}" 2>/dev/null || true)
if [[ -n "$STATUS" ]]; then
  LEVEL=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('level',1))" 2>/dev/null || echo "1")
  NAME=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name','glm'))" 2>/dev/null || echo "glm")
  echo "[msc: Level ${LEVEL} (${NAME})]"
fi
