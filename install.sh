#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════
# MSC — Model Steer Claude — Installer
# Installs as a Claude Code plugin at ~/.claude/plugins/msc/
# ═══════════════════════════════════════════

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$HOME/.claude/plugins/msc"
MSC_DIR="$HOME/.msc"
CLAUDE_DIR="$HOME/.claude"
SETTINGS="$CLAUDE_DIR/settings.json"

# Detect shell config
if [ -f "$HOME/.zshrc" ]; then
  SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
  SHELL_RC="$HOME/.bashrc"
else
  SHELL_RC="$HOME/.zshrc"
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[msc] $*"; }
ok()    { echo "[msc] ✓ $*"; }
warn()  { echo "[msc] ⚠ $*"; }

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
do_install() {
    echo ""
    echo "  ╔══════════════════════════════════╗"
    echo "  ║  MSC — Model Steer Claude        ║"
    echo "  ║  智能模型路由代理                  ║"
    echo "  ╚══════════════════════════════════╝"
    echo ""

    # 1. Plugin directory — copy/link to ~/.claude/plugins/msc/
    info "Installing plugin to $PLUGIN_DIR"
    mkdir -p "$(dirname "$PLUGIN_DIR")"
    if [ -L "$PLUGIN_DIR" ]; then
        rm "$PLUGIN_DIR"
    elif [ -d "$PLUGIN_DIR" ]; then
        rm -rf "$PLUGIN_DIR"
    fi
    ln -sf "$REPO_DIR" "$PLUGIN_DIR"
    ok "Plugin linked: $PLUGIN_DIR → $REPO_DIR"

    # 2. Config — ~/.msc/config.json
    mkdir -p "$MSC_DIR"
    if [ ! -f "$MSC_DIR/config.json" ]; then
        cp "$REPO_DIR/config/default-config.json" "$MSC_DIR/config.json"
        info "Created ~/.msc/config.json"
        warn "Edit ~/.msc/config.json to add your API keys"
    else
        ok "Config exists, skipping"
    fi

    # 3. Hooks — inject into global settings.json (with MSC_ENABLED guard)
    install_hooks

    # 4. Shell functions — cr() and crd()
    install_shell

    # 5. Remove legacy global skills (if present)
    for s in smoke redbull think-level model-steer; do
        if [ -d "$CLAUDE_DIR/skills/$s" ]; then
            rm -rf "$CLAUDE_DIR/skills/$s"
            info "Removed legacy global skill: $s"
        fi
    done
    for c in smoke redbull think-level; do
        if [ -f "$CLAUDE_DIR/commands/$c.md" ]; then
            rm -f "$CLAUDE_DIR/commands/$c.md"
            info "Removed legacy global command: $c"
        fi
    done

    echo ""
    echo "  ────────────────────────────────────"
    echo ""
    ok "Installation complete!"
    echo ""
    echo "  Next steps:"
    echo "    1. source $SHELL_RC"
    echo "    2. cr           ← 启动 Claude Code (通过 MSC 路由)"
    echo "    3. crd          ← 打开 Dashboard"
    echo ""
    echo "  也可以直接用 Claude Code 加载插件:"
    echo "    claude --plugin-dir ~/.claude/plugins/msc"
    echo ""
    echo "  命令 (仅在 cr 模式下可用):"
    echo "    /smoke          ← 切到最便宜模型"
    echo "    /redbull         ← 切到最强模型"
    echo "    /think-level N  ← 指定模型等级"
    echo ""
}

install_hooks() {
    info "Cleaning up legacy hooks from settings.json"
    mkdir -p "$CLAUDE_DIR"

    if [ -f "$SETTINGS" ]; then
        # Remove old MSC hooks from global settings (now self-registered via hooks/hooks.json)
        python3 -c "
import json

settings_path = '$SETTINGS'
with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.get('hooks', {})
changed = False
for key in list(hooks.keys()):
    before = len(hooks[key])
    hooks[key] = [e for e in hooks[key] if 'model-steer-claude' not in str(e) and '/msc/hooks/' not in str(e)]
    if len(hooks[key]) != before:
        changed = True
    if not hooks[key]:
        del hooks[key]

if changed:
    with open(settings_path, 'w') as f:
        json.dump(settings, f, indent=2)
        f.write('\n')
    print('[msc] Removed legacy hooks from settings.json')
else:
    print('[msc] No legacy hooks to clean')
"
    fi
    ok "Hooks defined in plugin hooks/hooks.json (auto-loaded by Claude Code)"
}

install_shell() {
    info "Installing shell functions"

    # Remove ALL existing cr()/crd()/# msc: blocks from shell config
    if grep -qE '^(cr|crd)\(\)|^# msc:' "$SHELL_RC" 2>/dev/null; then
        python3 << PYEOF
import sys

rc_path = "$SHELL_RC"
with open(rc_path) as f:
    lines = f.readlines()

out = []
skip_depth = 0  # track nested braces
skipping = False

for line in lines:
    stripped = line.strip()

    # Start skipping on cr()/crd() definition or # msc: comment
    if not skipping and (stripped.startswith("cr()") or stripped.startswith("crd()") or stripped.startswith("# msc:")):
        skipping = True
        skip_depth = 0

    if skipping:
        skip_depth += line.count("{") - line.count("}")
        # Stop skipping when braces balanced and we've seen at least one
        if skip_depth <= 0 and "{" in "".join(lines[:lines.index(line)+1]):
            if stripped == "}" or skip_depth <= 0:
                skipping = False
                skip_depth = 0
        continue

    out.append(line)

with open(rc_path, "w") as f:
    f.write("".join(out).rstrip() + "\n")

removed = len(lines) - len(out)
if removed > 0:
    print(f"[msc] Removed {removed} lines of old cr()/crd() definitions")
else:
    print("[msc] No old definitions found")
PYEOF
    fi

    cat >> "$SHELL_RC" << 'SHELL_EOF'

# msc: Claude Code intelligent model router (https://github.com/Libeny/model-steer-claude)
cr() {
  [ "$1" = "dashboard" ] && { crd; return; }
  # Start MSC proxy if not running
  curl --noproxy '*' -s http://127.0.0.1:3457/ &>/dev/null || {
    PYTHONUNBUFFERED=1 nohup python3 ~/.claude/plugins/msc/proxy.py >> /tmp/msc-proxy.log 2>&1 &
    for i in {1..20}; do curl --noproxy '*' -s http://127.0.0.1:3457/ &>/dev/null && break; sleep 0.2; done
  }
  unset ANTHROPIC_AUTH_TOKEN
  MSC_ENABLED=1 NO_PROXY=127.0.0.1 ANTHROPIC_BASE_URL=http://127.0.0.1:3457 \
    claude --plugin-dir ~/.claude/plugins/msc \
    --append-system-prompt-file ~/.msc/routing-prompt.md "$@"
}
crd() {
  curl --noproxy '*' -s http://127.0.0.1:3457/ &>/dev/null || {
    echo "[msc] Proxy not running. Start with: cr"; return 1
  }
  open "http://127.0.0.1:3457/ui"
}
SHELL_EOF

    ok "cr() and crd() added to $SHELL_RC"
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
do_uninstall() {
    echo ""
    info "Uninstalling MSC..."

    # Remove hooks from settings.json
    if [ -f "$SETTINGS" ]; then
        python3 -c "
import json
with open('$SETTINGS') as f:
    settings = json.load(f)
hooks = settings.get('hooks', {})
for key in list(hooks.keys()):
    hooks[key] = [e for e in hooks[key] if 'model-steer-claude' not in str(e) and '/msc/hooks/' not in str(e)]
    if not hooks[key]: del hooks[key]
if not hooks: settings.pop('hooks', None)
with open('$SETTINGS', 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
"
        ok "Hooks removed"
    fi

    # Remove plugin link
    [ -L "$PLUGIN_DIR" ] && rm "$PLUGIN_DIR" && ok "Plugin link removed"
    [ -d "$PLUGIN_DIR" ] && rm -rf "$PLUGIN_DIR" && ok "Plugin dir removed"

    # Remove legacy global skills/commands
    for s in smoke redbull think-level model-steer; do
        rm -rf "$CLAUDE_DIR/skills/$s" 2>/dev/null
    done
    for c in smoke redbull think-level; do
        rm -f "$CLAUDE_DIR/commands/$c.md" 2>/dev/null
    done

    # Remove shell functions
    if grep -q '# msc:' "$SHELL_RC" 2>/dev/null; then
        python3 -c "
import re
with open('$SHELL_RC') as f:
    content = f.read()
content = re.sub(r'\n# msc: .*?(?=\n[^# \n]|\n# [^m]|\Z)', '', content, flags=re.DOTALL)
with open('$SHELL_RC', 'w') as f:
    f.write(content.rstrip() + '\n')
"
        ok "Shell functions removed from $SHELL_RC"
    fi

    echo ""
    read -p "[msc] Also remove config (~/.msc/)? (y/N) " confirm
    if [[ "$confirm" == [yY] ]]; then
        rm -rf "$MSC_DIR"
        ok "Config removed"
    fi

    ok "Uninstall complete. Run 'source $SHELL_RC' to apply."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "${1:-install}" in
    install|--install) do_install ;;
    uninstall|--uninstall) do_uninstall ;;
    *)
        echo "Usage: $0 [install|uninstall]"
        echo "  install    Install MSC as Claude Code plugin (default)"
        echo "  uninstall  Remove MSC completely"
        exit 1
        ;;
esac
