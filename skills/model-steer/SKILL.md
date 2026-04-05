---
name: model-steer
description: |
  MSC 模型路由。路由规则由 system prompt 注入，此 skill 仅提供切换机制。
  相关命令：/smoke（省钱）、/redbull（全力）、/think-level N（指定等级）
---

# MSC — 模型切换机制

## 如何切换

```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/think-level?level=N"
```

Proxy 自动检测当前 session，无需传 session 参数。

## 快捷命令

| 命令 | 效果 |
|------|------|
| `/smoke` | 切到最低等级 |
| `/redbull` | 切到最高等级 |
| `/think-level N` | 切到指定等级 |
