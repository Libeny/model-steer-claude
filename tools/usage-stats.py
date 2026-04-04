#!/usr/bin/env python3
"""
Claude Code Session Usage Analyzer

统计 Claude Code session 的 token 用量和费用，支持按 session 或按目录分析。

Usage:
    # 分析单个 session（支持模糊搜索）
    python3 usage-stats.py <session-id>
    python3 usage-stats.py <partial-id>          # 模糊匹配 session ID
    python3 usage-stats.py <session.jsonl>

    # 分析目录下所有 session
    python3 usage-stats.py --dir <directory>
    python3 usage-stats.py --dir ~/.claude/projects/-Users-limuyu/

    # 默认分析当前用户的所有 session
    python3 usage-stats.py --all

    # 输出格式
    python3 usage-stats.py --all --format json
    python3 usage-stats.py --all --format table  (default)

    # 只看最近 N 个 session
    python3 usage-stats.py --all --recent 5

    # 按模型筛选
    python3 usage-stats.py --all --model claude-opus-4-6
    python3 usage-stats.py --all --model glm-5.1
"""

import json
import os
import sys
import glob
import argparse
from datetime import datetime
from pathlib import Path

# ============================================================
# Pricing (per million tokens, USD)
# ============================================================
PRICING = {
    # Claude Opus 4.6 - platform.claude.com official pricing
    "claude-opus-4-6": {
        "input": 5.0,
        "output": 25.0,
        "cache_create": 10.0,  # 1-hour TTL (2x input)
        "cache_read": 0.50,    # 0.1x input
        "label": "Claude Opus 4.6",
    },
    # Claude Opus 4.5
    "claude-opus-4-5-20251101": {
        "input": 5.0,
        "output": 25.0,
        "cache_create": 10.0,
        "cache_read": 0.50,
        "label": "Claude Opus 4.5",
    },
    # Claude Sonnet 4.6
    "claude-sonnet-4-6": {
        "input": 1.50,
        "output": 7.50,
        "cache_create": 3.0,
        "cache_read": 0.15,
        "label": "Claude Sonnet 4.6",
    },
    # Claude Haiku 4.5
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "output": 4.0,
        "cache_create": 1.0,
        "cache_read": 0.08,
        "label": "Claude Haiku 4.5",
    },
    # GLM 5.1 - 智谱官方 (based on GLM-5 reference: ¥4/¥18 per MTok)
    "glm-5.1": {
        "input": 4.0 / 7.25,       # ¥4/MTok → ~$0.55
        "output": 18.0 / 7.25,     # ¥18/MTok → ~$2.48
        "cache_create": 0.0,
        "cache_read": 1.0 / 7.25,  # ¥1/MTok → ~$0.14
        "label": "GLM 5.1 (智谱定价)",
        "cny": {"input": 4.0, "output": 18.0, "cache_create": 0.0, "cache_read": 1.0},
    },
}

# Claude-equivalent pricing for GLM (for comparison)
PRICING_AS_CLAUDE = {
    "glm-5.1": {
        "input": 5.0,
        "output": 25.0,
        "cache_create": 10.0,
        "cache_read": 0.50,
        "label": "GLM 5.1 (按 Claude 定价)",
    },
}


def get_default_session_dir():
    """Get the default Claude Code session directory."""
    home = Path.home()
    # Find project dirs
    claude_dir = home / ".claude" / "projects"
    if claude_dir.exists():
        # Find the most recent project dir
        project_dirs = sorted(claude_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if project_dirs:
            return str(project_dirs[0])
    return str(claude_dir)


def fuzzy_find_session(partial):
    """
    Fuzzy search for session files matching a partial ID.

    When the argument is not a file path and not a full UUID, search across
    all subdirectories under ~/.claude/projects/ for *<partial>*.jsonl matches.
    Returns a list of matching file paths, sorted by modification time (newest first).
    """
    home = Path.home()
    projects_dir = home / ".claude" / "projects"
    if not projects_dir.exists():
        return []

    pattern = f"*{partial}*.jsonl"
    matches = []
    for subdir in projects_dir.iterdir():
        if not subdir.is_dir():
            continue
        for f in subdir.glob(pattern):
            if ".backup" not in f.name:
                matches.append(str(f))

    # Sort by modification time, newest first
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return matches


def parse_session(filepath):
    """Parse a session JSONL file and extract usage data."""
    session = {
        "session_id": Path(filepath).stem,
        "filepath": filepath,
        "models": {},
        "total": {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0},
        "turns": 0,
        "start_time": None,
        "end_time": None,
        "user_messages": 0,
        "assistant_messages": 0,
    }

    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = obj.get("timestamp")

            if obj.get("type") == "user" and not obj.get("isMeta"):
                session["user_messages"] += 1
                if ts:
                    if not session["start_time"] or ts < session["start_time"]:
                        session["start_time"] = ts
                    if not session["end_time"] or ts > session["end_time"]:
                        session["end_time"] = ts

            if obj.get("type") != "assistant":
                continue

            msg = obj.get("message", {})
            usage = msg.get("usage", {})
            model = msg.get("model", "unknown")

            if model == "<synthetic>" or not usage:
                continue

            if ts:
                if not session["start_time"] or ts < session["start_time"]:
                    session["start_time"] = ts
                if not session["end_time"] or ts > session["end_time"]:
                    session["end_time"] = ts

            session["assistant_messages"] += 1

            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            cc = usage.get("cache_creation_input_tokens", 0)
            cr = usage.get("cache_read_input_tokens", 0)

            if model not in session["models"]:
                session["models"][model] = {
                    "input": 0, "output": 0, "cache_create": 0, "cache_read": 0,
                    "messages": 0,
                }
            session["models"][model]["input"] += inp
            session["models"][model]["output"] += out
            session["models"][model]["cache_create"] += cc
            session["models"][model]["cache_read"] += cr
            session["models"][model]["messages"] += 1

            session["total"]["input"] += inp
            session["total"]["output"] += out
            session["total"]["cache_create"] += cc
            session["total"]["cache_read"] += cr

    session["turns"] = session["user_messages"]
    return session


def calc_cost(stats, model):
    """Calculate cost for a model's usage."""
    p = PRICING.get(model, {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0})
    cost = {
        "input": stats["input"] / 1e6 * p["input"],
        "output": stats["output"] / 1e6 * p["output"],
        "cache_create": stats["cache_create"] / 1e6 * p["cache_create"],
        "cache_read": stats["cache_read"] / 1e6 * p["cache_read"],
    }
    cost["total"] = sum(cost.values())

    # CNY for GLM
    if "cny" in p:
        cny = p["cny"]
        cost["cny"] = {
            "input": stats["input"] / 1e6 * cny["input"],
            "output": stats["output"] / 1e6 * cny["output"],
            "cache_create": stats["cache_create"] / 1e6 * cny["cache_create"],
            "cache_read": stats["cache_read"] / 1e6 * cny["cache_read"],
        }
        cost["cny"]["total"] = sum(cost["cny"].values())

    # Claude-equivalent cost for GLM
    if model in PRICING_AS_CLAUDE:
        pac = PRICING_AS_CLAUDE[model]
        cost["as_claude"] = {
            "input": stats["input"] / 1e6 * pac["input"],
            "output": stats["output"] / 1e6 * pac["output"],
            "cache_create": stats["cache_create"] / 1e6 * pac["cache_create"],
            "cache_read": stats["cache_read"] / 1e6 * pac["cache_read"],
        }
        cost["as_claude"]["total"] = sum(cost["as_claude"].values())

    return cost


def format_timestamp(ts_str):
    """Format ISO timestamp to readable string."""
    if not ts_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_str[:16]


def print_session_detail(session):
    """Print detailed usage for a single session."""
    sid = session["session_id"]
    models = "+".join(sorted(session["models"].keys()))
    total_tokens = sum(session["total"].values())

    print(f"Session: {sid}")
    print(f"Models:  {models}")
    print(f"Time:    {format_timestamp(session['start_time'])} → {format_timestamp(session['end_time'])}")
    print(f"Turns:   {session['turns']} user messages, {session['assistant_messages']} assistant messages")
    print(f"Tokens:  {total_tokens:,}")
    print()

    grand_cost = 0
    grand_cost_as_claude = 0

    for model in sorted(session["models"].keys()):
        stats = session["models"][model]
        model_total = sum(stats.values()) - stats.get("messages", 0)
        cost = calc_cost(stats, model)
        grand_cost += cost["total"]
        label = PRICING.get(model, {}).get("label", model)

        print(f"  {label} ({stats.get('messages', 0)} messages)")
        print(f"  {'─' * 66}")
        print(f"  {'Token 类型':<18s} {'数量':>12s} {'单价 ($/MTok)':>14s} {'费用 ($)':>10s}")
        print(f"  {'Input':<18s} {stats['input']:>12,} {'':>14s} ${cost['input']:>9.4f}")
        print(f"  {'Output':<18s} {stats['output']:>12,} {'':>14s} ${cost['output']:>9.4f}")
        print(f"  {'Cache create':<18s} {stats['cache_create']:>12,} {'':>14s} ${cost['cache_create']:>9.4f}")
        print(f"  {'Cache read':<18s} {stats['cache_read']:>12,} {'':>14s} ${cost['cache_read']:>9.4f}")
        print(f"  {'小计':<18s} {model_total:>12,} {'':>14s} ${cost['total']:>9.4f}")

        if "cny" in cost:
            print(f"  {'CNY 小计':<18s} {'':>12s} {'':>14s} ¥{cost['cny']['total']:>9.2f}")

        if "as_claude" in cost:
            grand_cost_as_claude += cost["as_claude"]["total"]
            saved = cost["as_claude"]["total"] - cost["total"]
            pct = (1 - cost["total"] / max(cost["as_claude"]["total"], 0.0001)) * 100
            print(f"  {'如按Claude定价':<16s} {'':>12s} {'':>14s} ${cost['as_claude']['total']:>9.4f}")
            print(f"  {'节省':<18s} {'':>12s} {'':>14s} ${saved:>9.4f} ({pct:.0f}%)")
        else:
            grand_cost_as_claude += cost["total"]

        print()

    print(f"  {'═' * 66}")
    print(f"  总费用 (各自官方定价):  ${grand_cost:.4f}")
    if grand_cost_as_claude != grand_cost:
        print(f"  总费用 (全按Claude定价): ${grand_cost_as_claude:.4f}")
        print(f"  混用节省:               ${grand_cost_as_claude - grand_cost:.4f} ({(1 - grand_cost / max(grand_cost_as_claude, 0.0001)) * 100:.0f}%)")

    # Cache efficiency
    total = session["total"]
    if total["cache_read"] + total["input"] > 0:
        cache_hit_rate = total["cache_read"] / (total["cache_read"] + total["input"]) * 100
        print(f"\n  Cache 命中率: {cache_hit_rate:.1f}%")
    print()


def print_summary_table(sessions, model_filter=None):
    """Print a summary table of all sessions."""
    print(f"{'Session ID':<14s} {'Models':<28s} {'Input':>10s} {'Output':>8s} {'Cache R':>10s} {'Cache W':>10s} {'Cost ($)':>10s}")
    print("─" * 94)

    grand = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0, "cost": 0}

    for s in sessions:
        if model_filter:
            if model_filter not in s["models"]:
                continue

        models_str = "+".join(sorted(s["models"].keys()))
        if len(models_str) > 27:
            models_str = models_str[:24] + "..."

        total_cost = 0
        for model, stats in s["models"].items():
            cost = calc_cost(stats, model)
            total_cost += cost["total"]

        t = s["total"]
        print(f"{s['session_id'][:12]:<14s} {models_str:<28s} {t['input']:>10,} {t['output']:>8,} {t['cache_read']:>10,} {t['cache_create']:>10,} ${total_cost:>9.4f}")

        grand["input"] += t["input"]
        grand["output"] += t["output"]
        grand["cache_create"] += t["cache_create"]
        grand["cache_read"] += t["cache_read"]
        grand["cost"] += total_cost

    print("─" * 94)
    print(f"{'TOTAL':<14s} {'':28s} {grand['input']:>10,} {grand['output']:>8,} {grand['cache_read']:>10,} {grand['cache_create']:>10,} ${grand['cost']:>9.4f}")
    print()

    # Per-model aggregation
    all_models = {}
    for s in sessions:
        for model, stats in s["models"].items():
            if model_filter and model != model_filter:
                continue
            if model not in all_models:
                all_models[model] = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
            for k in ["input", "output", "cache_create", "cache_read"]:
                all_models[model][k] += stats[k]

    if len(all_models) > 1 or any(m in PRICING_AS_CLAUDE for m in all_models):
        print("按模型汇总:")
        print()
        total_official = 0
        total_as_claude = 0
        for model in sorted(all_models.keys()):
            stats = all_models[model]
            cost = calc_cost(stats, model)
            label = PRICING.get(model, {}).get("label", model)
            total_official += cost["total"]

            tokens = stats["input"] + stats["output"] + stats["cache_create"] + stats["cache_read"]
            print(f"  {label}: {tokens:,} tokens → ${cost['total']:.4f}", end="")
            if "cny" in cost:
                print(f" (¥{cost['cny']['total']:.2f})", end="")
            print()

            if "as_claude" in cost:
                total_as_claude += cost["as_claude"]["total"]
                print(f"    如按 Claude 定价: ${cost['as_claude']['total']:.4f} (节省 {(1 - cost['total'] / max(cost['as_claude']['total'], 0.0001)) * 100:.0f}%)")
            else:
                total_as_claude += cost["total"]

        if total_as_claude != total_official:
            print()
            print(f"  官方定价总计: ${total_official:.4f}")
            print(f"  全按Claude:   ${total_as_claude:.4f}")
            print(f"  混用节省:     ${total_as_claude - total_official:.4f} ({(1 - total_official / max(total_as_claude, 0.0001)) * 100:.0f}%)")
        print()


def output_json(sessions):
    """Output results as JSON."""
    result = []
    for s in sessions:
        entry = {
            "session_id": s["session_id"],
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "turns": s["turns"],
            "total_tokens": s["total"],
            "models": {},
        }
        for model, stats in s["models"].items():
            cost = calc_cost(stats, model)
            entry["models"][model] = {
                "tokens": {k: v for k, v in stats.items() if k != "messages"},
                "messages": stats.get("messages", 0),
                "cost_usd": round(cost["total"], 6),
            }
            if "cny" in cost:
                entry["models"][model]["cost_cny"] = round(cost["cny"]["total"], 4)
            if "as_claude" in cost:
                entry["models"][model]["cost_as_claude_usd"] = round(cost["as_claude"]["total"], 6)

        result.append(entry)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Claude Code Session Usage Analyzer")
    parser.add_argument("session", nargs="?", help="Session ID, partial ID (fuzzy search), or .jsonl file path")
    parser.add_argument("--dir", help="Directory containing session .jsonl files")
    parser.add_argument("--all", action="store_true", help="Analyze all sessions in default directory")
    parser.add_argument("--recent", type=int, default=0, help="Only analyze N most recent sessions")
    parser.add_argument("--model", help="Filter by model name")
    parser.add_argument("--format", choices=["table", "json"], default="table", help="Output format")
    args = parser.parse_args()

    # Determine which files to analyze
    files = []

    if args.session:
        # Single session
        if os.path.isfile(args.session):
            files = [args.session]
        else:
            # Try as session ID in default dir first
            session_dir = get_default_session_dir()
            candidate = os.path.join(session_dir, f"{args.session}.jsonl")
            if os.path.isfile(candidate):
                files = [candidate]
            else:
                # Fuzzy search across all project directories
                matches = fuzzy_find_session(args.session)
                if len(matches) == 1:
                    files = matches
                elif len(matches) > 1:
                    print(f"Found {len(matches)} sessions matching '{args.session}':")
                    for m in matches[:10]:
                        sid = Path(m).stem[:12]
                        proj = Path(m).parent.name
                        mtime = datetime.fromtimestamp(os.path.getmtime(m)).strftime("%Y-%m-%d %H:%M")
                        print(f"  {sid}  {proj:<40s}  {mtime}")
                    if len(matches) > 10:
                        print(f"  ... and {len(matches) - 10} more")
                    print(f"\nTip: use a longer partial ID to narrow down, or pass the full path.")
                    sys.exit(0)
                else:
                    print(f"Error: Session not found: {args.session}")
                    print(f"Searched default dir and all subdirs under ~/.claude/projects/")
                    sys.exit(1)
    elif args.dir:
        files = sorted(glob.glob(os.path.join(args.dir, "*.jsonl")),
                       key=os.path.getmtime, reverse=True)
        files = [f for f in files if ".backup" not in f]
    elif args.all:
        session_dir = get_default_session_dir()
        files = sorted(glob.glob(os.path.join(session_dir, "*.jsonl")),
                       key=os.path.getmtime, reverse=True)
        files = [f for f in files if ".backup" not in f]
    else:
        parser.print_help()
        sys.exit(0)

    if args.recent > 0:
        files = files[:args.recent]

    # Parse all sessions
    sessions = []
    for f in files:
        try:
            s = parse_session(f)
            if sum(s["total"].values()) > 0:
                sessions.append(s)
        except Exception as e:
            print(f"Warning: Failed to parse {f}: {e}", file=sys.stderr)

    if not sessions:
        print("No sessions with usage data found.")
        sys.exit(0)

    # Output
    if args.format == "json":
        output_json(sessions)
    elif len(sessions) == 1:
        print_session_detail(sessions[0])
    else:
        print(f"Found {len(sessions)} sessions with usage data\n")
        print_summary_table(sessions, model_filter=args.model)

        # If single session requested via filter, show detail
        if args.session:
            print_session_detail(sessions[0])


if __name__ == "__main__":
    main()
