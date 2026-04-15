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

MSC fixes this. It's a local proxy between Claude Code and model APIs, providing **three layers of protection**:

| Layer | Capability | Description |
|-------|-----------|-------------|
| **Layer 1: Never Fallback** | Automatic failover | Sonnet 429? Switch to GLM. GLM down? Continue down the chain. Smart error code classification avoids wasted requests |
| **Layer 2: Task Routing** | AI-driven model switching | With `--route`, the AI picks the right model per task — cheap model for chat, Sonnet for code, Opus for architecture |
| **Layer 3: Agent Orchestration** | Sub-agent model assignment | Each agent uses the model best suited for its task (planned) |

## How it works

```
                          ┌─ Sonnet (Anthropic)  ── 429/500? ──┐
User → Claude Code → MSC ─┤                                       ├→ Never break
   Proxy (local)           └─ GLM / DeepSeek / Moonshot ... ─────┘
```

### Layer 1: Never Fallback (enabled by default)

MSC proxies all API requests. When the primary model is unavailable, it automatically falls back:

```
Sonnet → GLM-5.1 → other available models
```

**Smart error classification** (not just HTTP status codes):

| Scenario | Action |
|----------|--------|
| Anthropic 429 (quota exhausted) | Fallback |
| Anthropic 500/503 (service error) | Fallback |
| GLM 1302/1303/1305 (temporary rate limit) | Fallback |
| GLM 1301 (content safety) | **No fallback** (same content triggers on any model) |
| GLM 1304/1308 (quota depleted) | **No fallback** (mark provider as unavailable) |
| GLM 1234 (network error, HTTP 400) | Fallback |

Error rules are configurable per provider. See `fallback.error_rules` in config.

### Layer 2: Task Routing (optional, `cr --route`)

By default `cr` only provides fallback protection. With `--route`, MSC injects routing rules into the system prompt and the AI switches models based on task complexity:

```
"Where's that file?"        → GLM (cheap, fast)
"Write unit tests"          → Sonnet (strong coding)
"Design microservice arch"  → Opus (deep reasoning)
```

Levels, models, and contexts are fully configurable via Dashboard with drag-and-drop ordering.

### Quota Check

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
cr --route            # Launch with fallback + AI routing
crd                   # Open Dashboard
crq                   # Check quota
```

## CLI commands

```bash
cr                          # Interactive session (fallback only)
cr --route                  # Interactive session (fallback + AI routing)
cr -p "explain this file"   # Print mode
cr --resume <session-id>    # Resume a session
crd                         # Open Dashboard
crq                         # Check quota status
```

In-session commands:

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

- **Model config** — add/remove/reorder models, set routing context per level
- **Usage analytics** — token breakdown, cost tracking, model distribution
- **CoT viewer** — browse conversation history across sessions

## Config

`~/.msc/config.json`:

```json
{
  "default_level": 2,
  "levels": {
    "1": {"name": "glm", "provider": "glm", "model": "glm-5.1", "context": "chat, Q&A"},
    "2": {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-4-6", "context": "coding, testing"},
    "3": {"name": "opus", "provider": "anthropic", "model": "claude-opus-4-6", "context": "architecture"}
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

`fallback.error_rules` supports any model provider: `_default` defines universal rules, per-provider keys define specific business error codes. Adding a new provider is just adding a new section.

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
├── proxy.py                     # Core proxy (fallback + error classification)
├── config/default-config.json   # Default config
├── ui/dashboard.html            # Dashboard SPA
└── install.sh                   # One-command setup
```

Key design decisions:

- **Plugin isolation** — MSC only loads via `cr`. Normal `claude` is unaffected.
- **Zero-retry fallback** — MSC doesn't retry, pure fail-fast fallback. Avoids stacking with Claude Code's built-in retry.
- **Error code priority** — Per-provider business error codes override HTTP status classification. GLM 1234 (network error, HTTP 400) correctly triggers fallback.
- **Signature patching** — GLM's empty thinking-block signatures are replaced with a valid placeholder for seamless cross-model sessions.

## Contact

- Email: libeny0526@gmail.com
- WeChat: BiothaLMY

## License

MIT
