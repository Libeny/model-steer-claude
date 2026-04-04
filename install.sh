#!/usr/bin/env bash
set -euo pipefail

# Resolve install directory (where this script lives)
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
MSC_DIR="$HOME/.msc"
CLAUDE_DIR="$HOME/.claude"
SETTINGS="$CLAUDE_DIR/settings.json"
ZSHRC="$HOME/.zshrc"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[msc] $*"; }
ok()    { echo "[msc] ✓ $*"; }
warn()  { echo "[msc] ⚠ $*"; }

# ---------------------------------------------------------------------------
# Step 1: --core
# ---------------------------------------------------------------------------
install_core() {
    info "Installing: core (proxy + config)"
    mkdir -p "$MSC_DIR"

    if [ ! -f "$MSC_DIR/config.json" ]; then
        cp "$INSTALL_DIR/config/default-config.json" "$MSC_DIR/config.json"
        info "Created ~/.msc/config.json from defaults"
        warn "Please edit ~/.msc/config.json and fill in your GLM key"
    else
        info "Config already exists, skipping"
    fi

    ok "Core installed"
}

# ---------------------------------------------------------------------------
# Step 2: --hooks
# ---------------------------------------------------------------------------
install_hooks() {
    info "Installing: hooks into settings.json"
    mkdir -p "$CLAUDE_DIR"

    # Create settings.json if it doesn't exist
    if [ ! -f "$SETTINGS" ]; then
        echo '{}' > "$SETTINGS"
    fi

    python3 -c "
import json, sys

settings_path = '$SETTINGS'
install_dir = '$INSTALL_DIR'

with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.setdefault('hooks', {})

# SessionStart hook
session_start = hooks.setdefault('SessionStart', [])
session_start_cmd = f'bash {install_dir}/hooks/session-start.sh'
already = any('model-steer-claude' in str(e) for e in session_start)
if not already:
    session_start.append({
        'matcher': '',
        'hooks': [{'type': 'command', 'command': session_start_cmd}]
    })
    print('[msc] Added SessionStart hook')
else:
    print('[msc] SessionStart hook already exists, skipping')

# UserPromptSubmit hook
prompt_submit = hooks.setdefault('UserPromptSubmit', [])
prompt_submit_cmd = f'bash {install_dir}/hooks/user-prompt-submit.sh'
already = any('model-steer-claude' in str(e) for e in prompt_submit)
if not already:
    prompt_submit.append({
        'matcher': '',
        'hooks': [{'type': 'command', 'command': prompt_submit_cmd}]
    })
    print('[msc] Added UserPromptSubmit hook')
else:
    print('[msc] UserPromptSubmit hook already exists, skipping')

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
"

    ok "Hooks installed"
}

# ---------------------------------------------------------------------------
# Step 3: --skills
# ---------------------------------------------------------------------------
install_skills() {
    info "Installing: skill + commands"

    # Install main skill
    skill_dir="$CLAUDE_DIR/skills/model-steer"
    mkdir -p "$skill_dir"
    cp "$INSTALL_DIR/skills/model-steer/SKILL.md" "$skill_dir/SKILL.md"
    info "Installed skill: model-steer"

    # Install slash commands
    cmd_dir="$CLAUDE_DIR/commands"
    mkdir -p "$cmd_dir"
    for cmd in smoke redbull think-level; do
        cp "$INSTALL_DIR/commands/$cmd.md" "$cmd_dir/$cmd.md"
        info "Installed command: /$cmd"
    done

    ok "Skill + commands installed"
}

# ---------------------------------------------------------------------------
# Step 4: --shell
# ---------------------------------------------------------------------------
install_shell() {
    info "Installing: cr() shell function into .zshrc"

    if grep -q '# msc:' "$ZSHRC" 2>/dev/null; then
        info "cr() already in .zshrc, skipping"
        ok "Shell installed"
        return
    fi

    cat >> "$ZSHRC" << SHELL_EOF

# msc: Claude Code intelligent model router
cr() {
  if [ "\$1" = "dashboard" ]; then
    crd
    return
  fi
  curl --noproxy '*' -s http://127.0.0.1:3457/ &>/dev/null || {
    PYTHONUNBUFFERED=1 nohup python3 ${INSTALL_DIR}/proxy.py >> /tmp/msc-proxy.log 2>&1 &
    for i in {1..20}; do curl --noproxy '*' -s http://127.0.0.1:3457/ &>/dev/null && break; sleep 0.2; done
  }
  CLAUDE_CODE_DISABLE_1M_CONTEXT=1 NO_PROXY=127.0.0.1 ANTHROPIC_BASE_URL=http://127.0.0.1:3457 claude "\$@"
}

# msc: dashboard shortcut
crd() {
  cr_pid=\$(lsof -ti:3457 2>/dev/null)
  if [ -z "\$cr_pid" ]; then
    echo "[msc] Proxy not running. Start with: cr"
    return 1
  fi
  open "http://127.0.0.1:3457/ui"
}
SHELL_EOF

    ok "Shell installed — run 'source ~/.zshrc' or open a new terminal"
}

# ---------------------------------------------------------------------------
# --uninstall
# ---------------------------------------------------------------------------
do_uninstall() {
    echo ""
    echo "This will remove:"
    echo "  - msc hooks from $SETTINGS"
    echo "  - skill: ~/.claude/skills/model-steer"
    echo "  - commands: ~/.claude/commands/{smoke,redbull,think-level}.md"
    echo "  - cr() function from $ZSHRC"
    echo "  - config dir: $MSC_DIR"
    echo ""
    read -p "[msc] Are you sure? (y/N) " confirm
    if [[ "$confirm" != [yY] ]]; then
        info "Uninstall cancelled"
        return
    fi

    # Remove hooks from settings.json
    if [ -f "$SETTINGS" ]; then
        info "Removing hooks from settings.json"
        python3 -c "
import json

settings_path = '$SETTINGS'
with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.get('hooks', {})
for key in ['SessionStart', 'UserPromptSubmit']:
    if key in hooks:
        hooks[key] = [e for e in hooks[key] if 'model-steer-claude' not in str(e)]
        if not hooks[key]:
            del hooks[key]

if not hooks:
    settings.pop('hooks', None)

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
"
        ok "Hooks removed"
    fi

    # Remove skill + commands
    rm -rf "$CLAUDE_DIR/skills/model-steer"
    for cmd in smoke redbull think-level; do
        rm -f "$CLAUDE_DIR/commands/$cmd.md"
    done
    ok "Skill + commands removed"

    # Remove cr() from .zshrc
    if [ -f "$ZSHRC" ] && grep -q '# msc:' "$ZSHRC"; then
        # Remove from '# msc:' line through the closing '}'
        python3 -c "
import re

with open('$ZSHRC') as f:
    content = f.read()

# Match the msc block: from '# msc:' to the closing '}' of crd() or cr()
pattern = r'\n?# msc:[^\n]*\n(?:cr|crd)\(\) \{.*?\n\}(?:\n\n# msc:[^\n]*\n(?:cr|crd)\(\) \{.*?\n\})*'
content = re.sub(pattern, '', content, flags=re.DOTALL)

with open('$ZSHRC', 'w') as f:
    f.write(content)
"
        ok "cr() removed from .zshrc"
    fi

    # Remove config dir
    rm -rf "$MSC_DIR"
    ok "Removed $MSC_DIR"

    ok "Uninstall complete"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo "[msc] MSC Installer"
    echo "[msc] Install dir: $INSTALL_DIR"
    echo ""

    # No args = install all
    if [ $# -eq 0 ]; then
        install_core
        install_hooks
        install_skills
        install_shell
        echo ""
        ok "All done! Run 'source ~/.zshrc' then 'cr' to start."
        return
    fi

    for arg in "$@"; do
        case "$arg" in
            --core)      install_core ;;
            --hooks)     install_hooks ;;
            --skills)    install_skills ;;
            --shell)     install_shell ;;
            --uninstall) do_uninstall ;;
            *)
                echo "Usage: $0 [--core] [--hooks] [--skills] [--shell] [--uninstall]"
                echo "  No flags = install all"
                exit 1
                ;;
        esac
    done
}

main "$@"
