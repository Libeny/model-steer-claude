<p align="center">
  <img src="docs/sailor.png" width="180" alt="MSC Captain">
  <h1 align="center">Model Steer Claude (MSC)</h1>
  <p align="center">
    <strong>Let Claude Code take the helm</strong>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
    <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Claude_Code-plugin-orange.svg" alt="Claude Code Plugin">
    <a href="README_CN.md">中文文档</a>
  </p>
</p>

---

## Why

Like a seasoned captain steering a ship, Claude Code should decide which model fits each task — not burn premium fuel on every mile.

As AI workers join daily workflows, two problems emerge:

1. **Cost** — Every message burns the same premium tokens, whether it's "where's that file?" or "design a distributed system"
2. **Right tool for the job** — Architecture decisions go to Opus, implementation to Sonnet, frontend UI to vision-capable models, routine tasks to fast cheap models

MSC gives Claude Code the helm. It picks the right model for each task — cheap models for routine work, powerful models for complex problems. When orchestrating sub-agents, each one gets the model that fits its specialty. Cost management and expertise allocation, handled by the AI itself.

## How it works

```
User → Claude Code → MSC Proxy (localhost:3457) → GLM / Sonnet / Opus
                         ↑
              AI reads routing prompt,
              runs curl to switch level
              before responding
```

- **Level 1** — cheap model (GLM): chat, Q&A, async callbacks
- **Level 2** — mid model (Sonnet): code, review, testing, debugging
- **Level 3** — top model (Opus): architecture, deep analysis

The routing rules come from config, editable via Dashboard. The proxy patches thinking-block signatures so cross-model sessions don't break.

## Quick start

```bash
git clone https://github.com/Libeny/model-steer-claude.git
cd model-steer-claude
bash install.sh

# Edit config — add your API keys
vim ~/.msc/config.json

# Source shell, then go
source ~/.zshrc
cr                    # Launch Claude Code through MSC
crd                   # Open Dashboard
```

## Usage

### CLI mode (`cr`)

`cr` wraps `claude` with the MSC plugin and routing prompt:

```bash
cr                          # Interactive session
cr -p "explain this file"   # Print mode
cr --resume <session-id>    # Resume a session
```

Inside a session, Claude auto-switches levels. You can also force it:

| Command | Effect |
|---------|--------|
| `/smoke` | Drop to cheapest model |
| `/redbull` | Switch to most powerful model |
| `/think-level N` | Switch to level N (1/2/3) |

### Agent SDK mode

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
- **Usage analytics** — token breakdown (input/output/cache), cost tracking, model distribution
- **CoT viewer** — browse conversation history across sessions

## Config

`~/.msc/config.json` — all settings in one file:

```json
{
  "levels": {
    "1": {
      "name": "glm",
      "provider": "glm",
      "model": "glm-5.1",
      "context": "chat, Q&A, scheduled tasks"
    },
    "2": {
      "name": "sonnet",
      "provider": "anthropic",
      "model": "claude-sonnet-4-6",
      "context": "coding, review, testing, debugging"
    }
  }
}
```

The `context` field drives routing — change it in Dashboard, the routing prompt regenerates automatically.

## Architecture

```
model-steer-claude/
├── .claude-plugin/plugin.json   # Plugin manifest
├── hooks/
│   ├── hooks.json               # Auto-registered hooks (SessionStart, UserPromptSubmit)
│   ├── session-start.sh         # Register session with proxy
│   └── user-prompt-submit.sh    # Show current level
├── skills/                      # /smoke, /redbull, /think-level, model-steer
├── commands/                    # Slash command definitions
├── proxy.py                     # Core proxy (~1100 lines)
├── config/default-config.json
├── ui/dashboard.html            # Dashboard SPA
└── install.sh                   # One-command setup
```

Key design decisions:

- **Plugin isolation** — MSC only loads when explicitly requested (`--plugin-dir`). Normal `claude` sees nothing.
- **System prompt injection** — Routing rules are appended via `--append-system-prompt-file`, not baked into CLAUDE.md.
- **Dynamic config** — Dashboard edits config, proxy regenerates `~/.msc/routing-prompt.md`, next session picks it up.
- **Signature patching** — GLM's empty thinking-block signatures are replaced with a valid placeholder, enabling seamless cross-model sessions.

## License

MIT
