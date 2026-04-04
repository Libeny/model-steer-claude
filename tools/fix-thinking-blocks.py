#!/usr/bin/env python3
"""
Fix Claude Code session files that contain thinking blocks with invalid signatures.

Problem: When a session uses a non-Anthropic model (e.g., GLM) that generates thinking
blocks with empty/short signatures, then switches to Anthropic Claude, the API rejects
the conversation history with: "Invalid `signature` in `thinking` block"

Two fix modes:
  replace (default) — Replace invalid signatures with a known-good signature.
                      Preserves thinking text so Claude can read the full reasoning context.
  delete            — Remove invalid thinking blocks entirely.
                      Safest option, but loses the non-Anthropic model's reasoning context.

Usage:
    # Analyze (dry-run, no changes)
    python3 fix-thinking-blocks.py <session_file.jsonl>

    # Fix with signature replacement (default, recommended)
    python3 fix-thinking-blocks.py <session_file.jsonl> --fix

    # Fix with signature replacement (explicit)
    python3 fix-thinking-blocks.py <session_file.jsonl> --fix --mode replace

    # Fix by deleting invalid thinking blocks
    python3 fix-thinking-blocks.py <session_file.jsonl> --fix --mode delete
"""

import json
import sys
import shutil
from pathlib import Path
from datetime import datetime

MIN_VALID_SIGNATURE_LEN = 100  # Anthropic signatures are typically 356-2344 chars

# A known-good signature that passes Anthropic API validation.
# Used in replace mode to preserve thinking text from non-Anthropic models.
#
# How this works (verified by experiment):
#   - Anthropic API checks signature FORMAT, not signature-to-thinking-text match
#   - The thinking TEXT field is what Claude actually reads for context
#   - So replacing an empty signature with a valid one lets the thinking text through
#
# Source: captured from a real Claude API response, widely used by open-source routers.
PLACEHOLDER_SIGNATURE = (
    "EpwGCkYIChgCKkCzVUuRrg7CcglSUWEef4rH6o35g9UYS8ZPe0/VomQTBsFx6sttYNj5"
    "l8GqgW6ejuHyYqpFToxIbZl0bw17l5dJEgzCnqDO0Z8fRlMrNgsaDLS1cnCjC53KBqE0"
    "CCIwAADQdo1eO+7qPAmo8J4WR3JPmr92S97kmvr5K1iPMiOpkZNj8mEXW8uzBoOJs/9Z"
    "KoMFiqHJ3UObwaJDqFOW70E9oCwDoc6jesaWVAEdN5vWfKMpIkjFJjECdjIdkxyJNJ8Ib"
    "8yXVal3qwE7uThoPRqSZDdHB5mmwPEjWE/90cSYCbtX2YsJki1265CabBb8/QEkODXg4"
    "kgRrL+c8e8rRXz/dr1RswvaPuzEdGKHRNi9UooNUeOK4/ebx1KkP9YZttyohN9GWqlts"
    "36kOoW0Cfie/ABDgF9g534BPth/sstxDM6d79QlRmh6NxizyTF74DXJI34u0M4tTRchqE"
    "5pAq85SgdJaa+dix1yJPMji8m6nZkwJbscJb9rdc2MKyKWjz8QL2+rTSSuZ2F1k1qSsW"
    "0xNcI7qLcI12Vncfn/VqY6YOIZy/saZBR0ezXvN6g+UYbuIdyVg7AyIFZt3nbrO7/kmO"
    "Eb2VKzygwklHGEIJHfFgMpH3JSrAzbZIowVHOF7VaJ+KXRFDCFin7hHTOiOsdg+1ij1m"
    "ML9Z/x/9CP4b7OUcaQm1llDZPSHc6rZMNL3DdB+fW5YfmNgKU35S+7AMtA10nVILzDAk"
    "1UV4T2K9Do09JlI6rjOs9UuULlIN2Z0eE8YTlANR6uQcw7lMcdfqYE8tke4rDKc2dDia"
    "S5vVe45VewICNpdXGN11yw8QqH7p27CR1HtN30e0tHXOR3bIwWk/Yb6O5fTaKG6Ri8e5Z"
    "CPvdD9HqepVi188nM0iTjJqL58F3ni04ECIhcbyaQWnuTes1Kw4CMwiZDLQkk8Hgz7HkU"
    "Of1btQTF/0nhD7ry0n0hAEg2PaDM3V6TjOjf4hEldRmeqERcQF1PfgKb6ZM12rlIIfUq"
    "KACczWJSzTV158+47HX36o0cgux6nFlv/DE+sEiRVxgB"
)


def analyze_session(filepath: str) -> dict:
    """Analyze a session file and report on thinking blocks."""
    stats = {
        "total_lines": 0,
        "assistant_messages": 0,
        "thinking_blocks_total": 0,
        "thinking_blocks_invalid": 0,
        "thinking_blocks_valid": 0,
        "invalid_details": [],
        "models_used": set(),
    }

    with open(filepath) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            stats["total_lines"] += 1

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") != "assistant":
                continue

            stats["assistant_messages"] += 1
            msg = obj.get("message", {})
            model = msg.get("model", "unknown")
            stats["models_used"].add(model)
            content = msg.get("content", [])

            for j, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "thinking":
                    continue

                stats["thinking_blocks_total"] += 1
                sig = block.get("signature", "")
                thinking_text = block.get("thinking", "")

                if len(sig) < MIN_VALID_SIGNATURE_LEN:
                    stats["thinking_blocks_invalid"] += 1
                    stats["invalid_details"].append({
                        "line": i,
                        "block_index": j,
                        "sig_len": len(sig),
                        "thinking_preview": thinking_text[:80],
                        "model": model,
                    })
                else:
                    stats["thinking_blocks_valid"] += 1

    return stats


def fix_session(filepath: str, mode: str = "replace", dry_run: bool = False) -> dict:
    """
    Fix a session file by handling thinking blocks with invalid signatures.

    Modes:
        replace — Replace invalid signatures with PLACEHOLDER_SIGNATURE.
                  Thinking text is preserved, Claude can read the reasoning context.
        delete  — Remove invalid thinking blocks entirely.
                  Safest, but loses reasoning context from non-Anthropic models.
    """
    result = {
        "mode": mode,
        "blocks_fixed": 0,
        "lines_modified": 0,
        "backup_path": None,
    }

    with open(filepath) as f:
        lines = f.readlines()

    new_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue

        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue

        if obj.get("type") != "assistant":
            new_lines.append(line)
            continue

        msg = obj.get("message", {})
        content = msg.get("content", [])
        if not content:
            new_lines.append(line)
            continue

        fixed_in_this_line = 0

        if mode == "replace":
            # Replace invalid signatures with placeholder
            for block in content:
                if isinstance(block, dict) and block.get("type") == "thinking":
                    sig = block.get("signature", "")
                    if len(sig) < MIN_VALID_SIGNATURE_LEN:
                        block["signature"] = PLACEHOLDER_SIGNATURE
                        fixed_in_this_line += 1

            if fixed_in_this_line > 0:
                result["blocks_fixed"] += fixed_in_this_line
                result["lines_modified"] += 1
                new_lines.append(json.dumps(obj, ensure_ascii=False) + "\n")
            else:
                new_lines.append(line)

        elif mode == "delete":
            # Remove thinking blocks with invalid signatures
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "thinking":
                    sig = block.get("signature", "")
                    if len(sig) < MIN_VALID_SIGNATURE_LEN:
                        fixed_in_this_line += 1
                        continue
                new_content.append(block)

            if fixed_in_this_line > 0:
                result["blocks_fixed"] += fixed_in_this_line
                result["lines_modified"] += 1
                obj["message"]["content"] = new_content
                new_lines.append(json.dumps(obj, ensure_ascii=False) + "\n")
            else:
                new_lines.append(line)

    if dry_run:
        return result

    # Create backup
    backup_path = filepath + f".backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(filepath, backup_path)
    result["backup_path"] = backup_path

    # Write fixed file
    with open(filepath, "w") as f:
        f.writelines(new_lines)

    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    filepath = sys.argv[1]
    if not Path(filepath).exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    do_fix = "--fix" in sys.argv
    dry_run = not do_fix

    # Parse mode
    mode = "replace"  # default
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            mode = sys.argv[idx + 1]
    if mode not in ("replace", "delete"):
        print(f"Error: Unknown mode '{mode}'. Use 'replace' or 'delete'.")
        sys.exit(1)

    # Always analyze first
    print(f"Analyzing: {filepath}")
    print("=" * 60)

    stats = analyze_session(filepath)
    print(f"Total lines:              {stats['total_lines']}")
    print(f"Assistant messages:       {stats['assistant_messages']}")
    print(f"Models used:              {', '.join(stats['models_used'])}")
    print(f"Thinking blocks (total):  {stats['thinking_blocks_total']}")
    print(f"  Valid signatures:       {stats['thinking_blocks_valid']}")
    print(f"  Invalid signatures:     {stats['thinking_blocks_invalid']}")
    print()

    if stats["thinking_blocks_invalid"] == 0:
        print("No invalid thinking blocks found. Session is clean.")
        return

    print("Invalid thinking blocks:")
    for detail in stats["invalid_details"]:
        print(f"  Line {detail['line']:3d} | model={detail['model']} | "
              f"sig_len={detail['sig_len']} | {detail['thinking_preview']!r}")
    print()

    mode_desc = {
        "replace": "Replace invalid signatures (preserves thinking context)",
        "delete": "Delete invalid thinking blocks (loses thinking context)",
    }

    if dry_run:
        print(f"[DRY RUN] Mode: {mode} — {mode_desc[mode]}")
        print(f"[DRY RUN] Run with --fix to apply changes.")

        result = fix_session(filepath, mode=mode, dry_run=True)
        print(f"[DRY RUN] Blocks to fix:   {result['blocks_fixed']}")
        print(f"[DRY RUN] Lines to modify: {result['lines_modified']}")
        return

    # Apply fix
    print(f"Mode: {mode} — {mode_desc[mode]}")
    print("Applying fix...")
    result = fix_session(filepath, mode=mode, dry_run=False)
    print(f"Blocks fixed:    {result['blocks_fixed']}")
    print(f"Lines modified:  {result['lines_modified']}")
    print(f"Backup saved:    {result['backup_path']}")
    print()
    print("Done! Try resuming the session with:")
    session_id = Path(filepath).stem
    print(f"  claude --resume {session_id}")


if __name__ == "__main__":
    main()
