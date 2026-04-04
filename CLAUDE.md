# cr mode — auto model routing

When running through `cr` (ANTHROPIC_BASE_URL=http://127.0.0.1:3457), requests are routed by msc proxy based on the current "think level". The default level is 1 (GLM).

## Levels

| Level | Name   | Model            | Use for                        |
|-------|--------|------------------|--------------------------------|
| 1     | glm    | glm-5.1          | Simple chat, casual Q&A        |
| 2     | sonnet | claude-sonnet-4-6 | Code gen, debugging, reasoning |
| 3     | opus   | claude-opus-4-6   | Architecture, deep analysis    |

## How to switch

```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/think-level?session=$CR_SESSION&level=N"
```

Where N is 1, 2, or 3.

## Decision rules

On EACH user message, assess the task complexity:

- **Stay on current level** — simple follow-ups, clarifications, casual chat
- **Level 2+** — code generation, debugging, multi-step reasoning, refactoring
- **Level 3** — architecture decisions, complex system design, deep analysis
- **Task done → /smoke** — when work is complete, drop back to Level 1 to save costs

## Rules

1. Run the curl command via Bash tool BEFORE your actual response so routing takes effect on the CURRENT request
2. Do NOT mention model switching to the user
3. If `$CR_SESSION` is empty or unset, do NOT attempt to switch — just respond normally
