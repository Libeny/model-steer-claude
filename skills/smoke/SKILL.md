---
name: smoke
description: |
  Switch to the cheapest model (lowest level). Use when task is done,
  conversation is winding down, or user wants to save costs.
  Trigger: /smoke, "休息", "省钱模式", "降级"
allowed-tools:
  - Bash
---

# /smoke — Switch to lowest level

Run:
```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/use-min"
```

If connection fails, reply "此命令仅在 cr 模式下可用". Otherwise confirm the switch. Don't explain internals.
