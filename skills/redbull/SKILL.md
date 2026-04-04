---
name: redbull
description: |
  Switch to the most powerful model (Opus, Level 3). Use when task requires
  deep thinking, complex architecture, or maximum capability.
  Trigger: /redbull, "全力", "深度思考", "升级"
allowed-tools:
  - Bash
---

# /redbull — Switch to Level 3 (Opus)

Read `$CR_SESSION` from env. If empty, reply "No active msc session." and stop.

Run:
```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/think-level?session=$CR_SESSION&level=3"
```

Confirm to the user: `[msc] Switched to Level 3 (Opus)`
