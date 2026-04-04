---
name: think-level
description: |
  Switch to a specific think level (1, 2, or 3).
  Usage: /think-level 1, /think-level 2, /think-level 3
allowed-tools:
  - Bash
---

# /think-level N — Switch think level

Parse the argument as the target level (1, 2, or 3). If no argument or invalid, reply with usage: `/think-level 1|2|3`

Read `$CR_SESSION` from env. If empty, reply "No active msc session." and stop.

Run:
```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/think-level?session=$CR_SESSION&level=N"
```

Where N is the parsed level number.

Confirm to the user:
- Level 1: `[msc] Switched to Level 1 (GLM)`
- Level 2: `[msc] Switched to Level 2 (Sonnet)`
- Level 3: `[msc] Switched to Level 3 (Opus)`
