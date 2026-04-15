<p align="center">
  <img src="docs/sailor.png" width="180" alt="MSC Captain">
  <h1 align="center">NB-Claude</h1>
  <p align="center">
    <strong>Never Break Your Flow</strong>
  </p>
  <p align="center">
    <em>High-availability model proxy for Claude Code, designed for AI digital workers & AI-Coding clusters</em>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
    <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Claude_Code-plugin-orange.svg" alt="Claude Code Plugin">
    <a href="README.md">中文文档</a>
  </p>
</p>

---

## Why NB-Claude

Claude Code is the most powerful coding agent today, but it has one hard limit: **when quota runs out, work stops**.

For a human that's tolerable — wait a bit, try again. But for **AI digital workers**, it's critical: a 429 breaks the entire task chain, downstream tasks stall, and nobody's there to manually restart.

NB-Claude fixes this. It's a local proxy between Claude Code and model APIs, ensuring **a model is always available**:

| Scenario | Without NB-Claude | With NB-Claude |
|----------|-------------------|----------------|
| Sonnet quota exhausted | Task interrupted, wait for reset | Auto-switch to GLM, seamless |
| API returns 500 | Claude Code exits | Auto-retry next provider |
| GLM temporary rate limit | Direct error | Auto-degrade to next available model |
| Primary model persistently down | Manual intervention | Circuit Breaker auto-bans, 5-min probe recovery |

## Three-Layer Architecture

NB-Claude provides three progressively enhanced capabilities, enabled on demand:

```
┌──────────────────────────────────────────────────┐
│  Layer 3: Agent Orchestration (planned)            │
│  Each sub-agent gets the model best suited for it  │
├──────────────────────────────────────────────────┤
│  Layer 2: Task Routing (cr --route)                │
│  You define rules, AI switches models accordingly  │
├──────────────────────────────────────────────────┤
│  Layer 1: Never Fallback (enabled by default)      │
│  Auto-degrade to next available model on failure    │
└──────────────────────────────────────────────────┘
```

---

## Layer 1: Never Fallback (enabled by default)

```
                          ┌─ Sonnet ── 429/500? ──┐
User → Claude Code → Proxy─┤                        ├→ Never break
                          └─ GLM / DeepSeek / ... ─┘
```

All API requests go through the local proxy. When the primary model is unavailable, it automatically degrades along your configured chain:

```
Sonnet → GLM-5.1 → other available models
```

### Smart Error Classification

Not just HTTP status codes — the proxy parses per-provider business error codes for precise fallback decisions:

| Error | Source | Action |
|-------|--------|--------|
| 429 / 500 / 503 | Anthropic | **Fallback** — quota exhausted or service error |
| 1302 / 1303 / 1305 / 1312 | GLM temporary rate limit | **Fallback** — short-term recoverable |
| 1234 | GLM network error | **Fallback** — server-side network issue |
| 1301 | GLM content safety | **No fallback** — same content triggers on any model |
| 1304 / 1308 | GLM quota depleted | **No fallback** — circuit-break the provider |

Error rules are fully configurable. `_default` defines universal rules, per-provider keys define vendor-specific codes. Adding a new provider is just adding a config section.

### Circuit Breaker

When quota exhaustion is detected (GLM 1304/1308, Anthropic 429 + quota API confirmation), the provider is immediately circuit-broken:

- **Broken state**: Subsequent requests skip the provider entirely, zero latency
- **Auto-recovery**: Probes every 5 minutes, automatically unbreaks when the provider recovers
- **Dashboard visibility**: Real-time view of which providers are broken, why, and when

---

## Layer 2: Task Routing (`cr --route`)

**You define the rules. AI switches models accordingly.**

By default `cr` only provides fallback protection. With `--route`, the proxy injects routing rules into the system prompt. Before each response, the AI evaluates the task type and switches to the appropriate model.

### How it works

```
User asks → AI reads routing rules → evaluates task type → curl to switch level → responds with that model
```

### Rules are entirely user-defined

On the Dashboard, write a **routing context description** for each model level. The AI uses these descriptions to make switching decisions:

| Level | Model | Routing Context | Example Task |
|-------|-------|----------------|-------------|
| 1 | GLM-5.1 | Chat, Q&A, file lookup, scheduled tasks | "Where's that file?" |
| 2 | Sonnet | Coding, review, testing, debugging, refactoring | "Write unit tests" |
| 3 | Opus | Architecture design, deep code audit, system decisions | "Design microservice arch" |
| N | Any model | You define the scenario | Vision model for frontend... |

**Flexibility**:

- Unlimited levels, unlimited models — add DeepSeek, Moonshot, Qwen anytime
- Each level's **routing context** is free-form — "scheduled tasks", "frontend UI", "data analysis"...
- Drag-and-drop to reorder priority, saved changes take effect on next session
- Manual override: `/smoke` (cheapest), `/redbull` (most powerful), `/think-level N`

---

## Layer 3: Agent Orchestration (planned)

In multi-agent collaboration scenarios, each sub-agent can use the model best suited for its task. Example: architecture agent uses Opus, coding agent uses Sonnet, testing agent uses a fast model. Deep integration with Claude Code Agent SDK.

---

## Quota Check

```bash
crq    # Check quota status and reset times for all models
```

Example output:

```
  Claude Subscription: pro

  5h         [█████████░░░░░░░░░░░]  45%  55% left    resets in 2h30m
  7d         [██████████████░░░░░░]  72%  28% left    resets in 6d12h
  7d Sonnet  [█████████████████░░░]  88%  12% left    resets in 6d12h

  ✓ glm: glm-5.1 (ok)
```

Quota info is fetched in real-time from the Anthropic usage API, helping you plan around time windows.

---

## Quick start

```bash
git clone https://github.com/Libeny/model-steer-claude.git
cd model-steer-claude
bash install.sh

# Edit config — add your API keys
vim ~/.msc/config.json

# Source shell, then go
source ~/.zshrc
cr                    # Launch with fallback protection
cr --route            # Launch with fallback + rule-based routing
crd                   # Open Dashboard
crq                   # Check quota
```

## CLI commands

```bash
cr                          # Interactive session (fallback only)
cr --route                  # Interactive session (fallback + rule-based routing)
cr -p "explain this file"   # Print mode
cr --resume <session-id>    # Resume a session
crd                         # Open Dashboard
crq                         # Check quota status
```

In-session commands (`--route` mode):

| Command | Effect |
|---------|--------|
| `/smoke` | Drop to cheapest model |
| `/redbull` | Switch to most powerful model |
| `/think-level N` | Switch to level N |

## Agent SDK mode

```python
from claude_agent_sdk import query, ClaudeAgentOptions
from pathlib import Path

MSC_PLUGIN = str(Path.home() / ".claude/plugins/msc")
ROUTING = (Path.home() / ".msc/routing-prompt.md").read_text()

async for msg in query(
    prompt="Implement a red-black tree with tests",
    options=ClaudeAgentOptions(
        plugins=[{"type": "local", "path": MSC_PLUGIN}],
        env={"MSC_ENABLED": "1"},
        system_prompt=ROUTING,
    ),
):
    print(msg.content)
```

Same plugin, same hooks, same routing — identical behavior to `cr`.

## Dashboard

`crd` opens a local web UI at `http://localhost:3457/ui`:

### Model Config

Manage model levels, ordering, and routing contexts:

<p align="center"><img src="docs/screenshot-models.png" width="700" alt="Model config"></p>

### Usage Analytics

Real-time cost and savings:

<p align="center"><img src="docs/screenshot-cost.png" width="700" alt="Cost panel"></p>

- **Mixed mode cost** — actual spend (¥/$ toggle)
- **Savings** — how much cheaper non-Claude tokens are vs. all-Sonnet
- **Model distribution** — token share per model
- **Project ranking** — aggregated per-project with model breakdown

### Fallback Protection

- **Circuit Breaker status** — real-time view of broken providers
- **Fallback event log** — every degradation recorded (from/to model, reason, time)

## Config

`~/.msc/config.json`:

```json
{
  "default_level": 2,
  "levels": {
    "1": {"name": "glm", "provider": "glm", "model": "glm-5.1", "context": "chat, Q&A, file lookup"},
    "2": {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-4-6", "context": "coding, testing, debugging"},
    "3": {"name": "opus", "provider": "anthropic", "model": "claude-opus-4-6", "context": "architecture, deep audit"}
  },
  "providers": {
    "glm": {"url": "https://open.bigmodel.cn/api/anthropic/v1/messages", "key": "..."},
    "anthropic": {"url": "https://api.anthropic.com", "passthrough_auth": true}
  },
  "fallback": {
    "error_rules": {
      "_default": {
        "retriable_http": [429, 500, 502, 503, 529],
        "fatal_http": [400, 401, 403, 404]
      },
      "glm": {
        "business_code_path": "error.code",
        "retriable_codes": ["1200", "1230", "1234", "1302", "1303", "1305", "1312"],
        "fatal_codes": ["1301", "1304", "1308", "1309", "1310", "1311", "1313",
                        "1000", "1001", "1002", "1003", "1004",
                        "1110", "1111", "1112", "1113", "1121"]
      }
    }
  }
}
```

Key fields:

| Field | Description |
|-------|-------------|
| `levels.N.context` | Routing context — AI uses this to decide when to switch to this model |
| `providers` | Model provider config — any Anthropic API-compatible endpoint |
| `fallback.error_rules` | Error classification — `_default` for universal, override per provider name |
| `fallback.error_rules.*.retriable_codes` | Business error codes that trigger fallback |
| `fallback.error_rules.*.fatal_codes` | Business error codes that should NOT trigger fallback |

## Architecture

```
model-steer-claude/
├── .claude-plugin/plugin.json   # Plugin manifest
├── hooks/
│   ├── hooks.json               # Auto-registered hooks
│   ├── session-start.sh         # Register session with proxy
│   └── user-prompt-submit.sh    # Show current level
├── skills/                      # /smoke, /redbull, /think-level
├── commands/                    # Slash command definitions
├── proxy.py                     # Core proxy (fallback + error classification + circuit breaker)
├── config/default-config.json   # Default config
├── ui/dashboard.html            # Dashboard SPA
└── install.sh                   # One-command setup
```

Key design decisions:

- **Plugin isolation** — NB-Claude only loads via `cr`. Normal `claude` is unaffected.
- **Zero-retry fallback** — Pure fail-fast degradation. Avoids stacking with Claude Code's built-in retry (3 models × 10 retries = 90 requests).
- **Error code priority** — Per-provider business error codes override HTTP status classification. GLM 1234 (HTTP 400) correctly triggers fallback.
- **Rule-driven routing** — Routing decisions are driven by user-defined context descriptions, not hardcoded. Config changes take effect immediately.
- **Signature patching** — Cross-model sessions auto-patch thinking-block signatures for seamless switching.

## Contact

- Email: libeny0526@gmail.com
- WeChat: BiothaLMY

## License

MIT
