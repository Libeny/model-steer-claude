---
name: smoke
description: |
  Switch to the cheapest model (GLM, Level 1). Use when task is done,
  conversation is winding down, or user wants to save costs.
  Trigger: /smoke, "休息", "省钱模式", "降级"
allowed-tools:
  - Bash
---

# /smoke — Switch to Level 1 (GLM)

Read `$CR_SESSION` from env. If empty, reply "No active msc session." and stop.

Run:
```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/think-level?session=$CR_SESSION&level=1"
```

Confirm to the user: `[msc] Switched to Level 1 (GLM)`
