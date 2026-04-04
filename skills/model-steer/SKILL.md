---
name: model-steer
description: |
  MSC 模型路由自动决策。当 $CR_SESSION 存在时，根据任务复杂度自动选择模型等级。
  不需要用户手动触发，Claude 自主判断。
  相关命令：/smoke（省钱）、/redbull（全力）、/think-level N（指定等级）
---

# MSC — 模型自动决策

当通过 `cr` 启动 Claude Code 时，MSC proxy 会路由请求到不同模型。你可以根据任务复杂度自主决策切换。

## 等级

| 等级 | 适用场景 |
|------|---------|
| 1 | 简单对话、闲聊、Q&A |
| 2 | 代码生成、调试、重构 |
| 3 | 架构设计、深度分析、复杂推理 |

## 如何切换

```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/think-level?session=$CR_SESSION&level=N"
```

## 自动决策规则

在每条用户消息前评估：
- 简单对话 → 留在当前等级
- 代码生成/调试 → 升到 2+
- 架构/复杂推理 → 升到 3
- 任务完成 → 降回 1

在响应前执行 curl，不向用户提及切换。`$CR_SESSION` 为空时不切换。

## 快捷命令

| 命令 | 效果 |
|------|------|
| `/smoke` | 切到等级 1（省钱模式） |
| `/redbull` | 切到等级 3（全力模式） |
| `/think-level N` | 切到指定等级 |
