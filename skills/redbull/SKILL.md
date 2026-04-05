---
name: redbull
description: |
  Switch to the most powerful model (highest level). Use when task requires
  deep thinking, complex architecture, or maximum capability.
  Trigger: /redbull, "全力", "深度思考", "升级"
allowed-tools:
  - Bash
---

# /redbull — Switch to highest level

Run:
```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/use-max"
```

If connection fails, reply "此命令仅在 cr 模式下可用". Otherwise confirm the switch. Don't explain internals.
