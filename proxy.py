#!/usr/bin/env python3
"""msc proxy: config-driven multi-level Claude routing with SQLite persistence."""
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from functools import lru_cache
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
MSC_DIR = Path.home() / ".msc"
CONFIG_PATH = MSC_DIR / "config.json"
DB_PATH = MSC_DIR / "msc.db"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "default-config.json"
DASHBOARD_PATH = Path(__file__).resolve().parent / "ui" / "dashboard.html"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# ---------------------------------------------------------------------------
# Signature patching
# ---------------------------------------------------------------------------
PLACEHOLDER_SIG = (
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


def fix_signatures(messages):
    """Fix placeholder signatures in request message history."""
    fixed = 0
    if not isinstance(messages, list):
        return 0
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                if len(block.get("signature", "")) < 100:
                    block["signature"] = PLACEHOLDER_SIG
                    fixed += 1
    return fixed


def patch_json_signatures(raw):
    """Patch signatures in a non-streaming GLM JSON response."""
    try:
        data = json.loads(raw)
        fixed = 0
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "thinking":
                if len(block.get("signature", "")) < 100:
                    block["signature"] = PLACEHOLDER_SIG
                    fixed += 1
        if fixed:
            print(f"[msc] Patched {fixed} GLM response signature(s)", flush=True)
            return json.dumps(data).encode()
    except Exception:
        pass
    return raw if isinstance(raw, bytes) else raw.encode()


class StreamSignaturePatcher:
    """Buffers SSE lines across chunks to handle split boundaries."""

    def __init__(self):
        self.buffer = ""

    def feed(self, chunk):
        self.buffer += chunk.decode()
        lines = self.buffer.split("\n")
        self.buffer = lines.pop()  # hold incomplete last line

        output = []
        for line in lines:
            output.append(self._patch_line(line))
        return "\n".join(output + [""]).encode()

    def _patch_line(self, line):
        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
            return line
        try:
            data = json.loads(line[6:])
            patched = False
            cb = data.get("content_block", {})
            if cb.get("type") == "thinking" and len(cb.get("signature", "")) < 100:
                cb["signature"] = PLACEHOLDER_SIG
                patched = True
            delta = data.get("delta", {})
            if delta.get("type") == "signature_delta" and len(delta.get("signature", "")) < 100:
                delta["signature"] = PLACEHOLDER_SIG
                patched = True
            if patched:
                print("[msc] Patched GLM stream signature", flush=True)
                return "data: " + json.dumps(data)
        except (json.JSONDecodeError, KeyError):
            pass
        return line


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    """Load config from ~/.msc/config.json, fallback to default."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    with open(DEFAULT_CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg):
    """Atomic write config to ~/.msc/config.json."""
    MSC_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=MSC_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        os.unlink(tmp)
        raise


def mask_key(key):
    """Mask a provider key, showing only first 8 chars."""
    if not key or len(key) <= 8:
        return key
    return key[:8] + "..." + "*" * 8


def build_route_levels(cfg):
    """Build {int_level: {name, provider, model, label, context}} from config."""
    levels = {}
    for k, v in cfg["levels"].items():
        lvl = int(k)
        levels[lvl] = {
            "name": v["name"],
            "provider": v["provider"],
            "model": v["model"],
            "label": v["name"].upper(),
            "context": v.get("context", ""),
        }
    return levels


def generate_routing_prompt(cfg):
    """Generate routing system prompt from config levels."""
    lines = [
        "# Model Routing",
        "",
        "You are behind an intelligent routing proxy. On EACH user message, assess the intent and switch to the appropriate level BEFORE responding.",
        "",
        "## Levels",
        "",
    ]
    for k in sorted(cfg["levels"].keys(), key=int):
        v = cfg["levels"][k]
        ctx = v.get("context", "")
        lines.append(f"- **Level {k} ({v['name']})**: {ctx}")
    lines += [
        "",
        "## How to switch",
        "",
        '```bash',
        'curl --noproxy \'*\' -s "http://127.0.0.1:3457/think-level?level=N"',
        '```',
        "",
        "## Rules",
        "",
        "1. Run the curl via Bash tool BEFORE your actual response",
        "2. Do NOT mention model switching to the user",
        "3. Always pick the LOWEST level that fits the task",
        "4. When task is done, drop back to Level 1",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db():
    """Initialize SQLite database with WAL mode."""
    MSC_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            level INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            level INTEGER,
            provider TEXT,
            model_name TEXT DEFAULT '',
            label TEXT DEFAULT '',
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_create_tokens INTEGER DEFAULT 0,
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_index (
            session_id TEXT PRIMARY KEY,
            project_path TEXT,
            first_query TEXT,
            last_timestamp TEXT,
            file_path TEXT,
            file_size INTEGER DEFAULT 0,
            indexed_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fallback_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            from_level INTEGER,
            from_label TEXT DEFAULT '',
            to_level INTEGER,
            to_label TEXT DEFAULT '',
            provider TEXT,
            reason TEXT DEFAULT '',
            timestamp TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    try:
        conn.execute("ALTER TABLE usage ADD COLUMN model_name TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE usage ADD COLUMN label TEXT DEFAULT ''")
    except Exception:
        pass
    conn.close()


def db_connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Session Indexer (CoT)
# ---------------------------------------------------------------------------
def _dir_to_project_path(dirname):
    """Convert directory name like '-Users-limuyu-work-model-steer-claude' to '~/work/model-steer-claude'.

    Claude Code encodes paths by replacing / with -.  Since - also appears in
    directory names, we greedily match against the filesystem from left to right,
    trying the longest segment first at each level.
    """
    raw = dirname.lstrip("-")
    parts = raw.split("-")

    resolved = []
    i = 0
    while i < len(parts):
        found = False
        for j in range(len(parts), i, -1):
            candidate = "-".join(parts[i:j])
            test_path = "/" + "/".join(resolved + [candidate])
            if Path(test_path).exists():
                resolved.append(candidate)
                i = j
                found = True
                break
        if not found:
            resolved.append(parts[i])
            i += 1

    path = "/" + "/".join(resolved)
    home = str(Path.home())
    if path.startswith(home):
        path = "~" + path[len(home):]
    return path


def _find_session_jsonl(session_id):
    """Find jsonl file for session_id under ~/.claude/projects/."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return None
    for proj_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        candidate = proj_dir / (session_id + ".jsonl")
        if candidate.exists():
            return candidate
    return None


def _is_command_message(text):
    """Check if text is a CLI command/system message, not a real user query."""
    if not text:
        return True
    t = text.strip()
    return (
        t.startswith("<command-") or
        t.startswith("<local-command-") or
        t.startswith("<system-reminder>") or
        t.startswith("Base directory for this skill") or
        t.startswith("/") or  # slash commands like /clear, /smoke
        not t  # empty
    )


def _extract_first_query(filepath):
    """Read file to find first real user message and extract query text."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "user":
                        msg = obj.get("message", {})
                        content = msg.get("content", "")
                        text = ""
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text", "")
                                    break
                                elif isinstance(block, str):
                                    text = block
                                    break
                        if _is_command_message(text):
                            continue  # skip command messages, find next user msg
                        return text[:500]
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass
    return ""


def _extract_last_timestamp(filepath):
    """Read last 16KB of file to find last timestamp."""
    try:
        size = os.path.getsize(filepath)
        read_size = min(size, 16384)
        with open(filepath, "rb") as f:
            f.seek(max(0, size - read_size))
            data = f.read().decode("utf-8", errors="replace")
        # Search from end for timestamp
        for line in reversed(data.strip().split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = obj.get("timestamp")
                if ts:
                    return ts
            except (json.JSONDecodeError, KeyError):
                continue
        # Fallback: try tac approach
        try:
            result = subprocess.run(
                ["grep", "-m1", '"timestamp"', filepath],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout:
                # Extract timestamp value
                m = re.search(r'"timestamp"\s*:\s*"([^"]+)"', result.stdout)
                if m:
                    return m.group(1)
        except Exception:
            pass
    except Exception:
        pass
    return ""


def _index_session(session_id):
    """Index a single session: find jsonl, extract metadata, store in DB."""
    filepath = _find_session_jsonl(session_id)
    if not filepath:
        return

    first_query = _extract_first_query(str(filepath))
    last_timestamp = _extract_last_timestamp(str(filepath))
    file_size = os.path.getsize(str(filepath))

    # Derive project_path from parent directory name
    project_path = _dir_to_project_path(filepath.parent.name)

    conn = db_connect()
    try:
        conn.execute(
            """INSERT INTO session_index (session_id, project_path, first_query, last_timestamp, file_path, file_size, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(session_id) DO UPDATE SET
                 project_path = excluded.project_path,
                 first_query = excluded.first_query,
                 last_timestamp = excluded.last_timestamp,
                 file_path = excluded.file_path,
                 file_size = excluded.file_size,
                 indexed_at = datetime('now')""",
            (session_id, project_path, first_query, last_timestamp, str(filepath), file_size),
        )
        conn.commit()
    finally:
        conn.close()


def _index_all_sessions():
    """Index all sessions registered in the sessions table."""
    conn = db_connect()
    try:
        rows = conn.execute("SELECT session_id FROM sessions").fetchall()
    finally:
        conn.close()

    for row in rows:
        try:
            _index_session(row["session_id"])
        except Exception as e:
            print(f"[msc] CoT index error for {row['session_id'][:8]}…: {e}", flush=True)

    print(f"[msc] CoT indexed {len(rows)} session(s)", flush=True)


def _periodic_index():
    """Periodically re-index all registered sessions."""
    while True:
        time.sleep(60)
        try:
            _index_all_sessions()
        except Exception as e:
            print(f"[msc] CoT periodic index error: {e}", flush=True)


def start_cot_indexer():
    """Start background indexer thread."""
    def _run():
        try:
            _index_all_sessions()
        except Exception as e:
            print(f"[msc] CoT startup index error: {e}", flush=True)
        _periodic_index()

    t = threading.Thread(target=_run, daemon=True, name="cot-indexer")
    t.start()


# ---------------------------------------------------------------------------
# CoT file reading utilities
# ---------------------------------------------------------------------------
def read_jsonl_tail(filepath, limit=20):
    """Read last N valid JSON lines without loading entire file."""
    try:
        size = os.path.getsize(filepath)
        chunk_size = min(size, limit * 4096)
        with open(filepath, "rb") as f:
            f.seek(max(0, size - chunk_size))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.strip().split("\n")
        result = []
        total_lines_in_file = _count_lines_cached(filepath, size)
        for i, line in enumerate(reversed(lines)):
            try:
                obj = json.loads(line.strip())
                obj["_line_number"] = total_lines_in_file - i
                result.insert(0, obj)
                if len(result) >= limit:
                    break
            except (json.JSONDecodeError, ValueError):
                pass
        return result
    except Exception:
        return []


def read_jsonl_head(filepath, offset=0, limit=20):
    """Read N valid JSON lines from start, skipping offset lines."""
    result = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            line_num = 0
            skipped = 0
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                obj["_line_number"] = line_num
                result.append(obj)
                if len(result) >= limit:
                    break
        return result
    except Exception:
        return []


def read_jsonl_before(filepath, before_line, limit=20):
    """Read up to limit messages with line_number < before_line (last N before that line)."""
    buf = deque(maxlen=limit)
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line_num, line in enumerate(f, 1):
                if line_num >= before_line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    obj["_line_number"] = line_num
                    buf.append(obj)
                except (json.JSONDecodeError, ValueError):
                    pass
        return list(buf)
    except Exception:
        return []


@lru_cache(maxsize=64)
def _count_lines_cached(filepath, file_size):
    """Count total lines in file (cached by path+size)."""
    try:
        result = subprocess.run(
            ["wc", "-l", filepath],
            capture_output=True, text=True, timeout=10
        )
        return int(result.stdout.strip().split()[0])
    except Exception:
        return 0


def count_messages_in_file(filepath):
    """Count message-type lines (user/assistant/system) in jsonl."""
    count = 0
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") in ("user", "assistant", "system", "progress"):
                        count += 1
                except (json.JSONDecodeError, ValueError):
                    pass
    except Exception:
        pass
    return count


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
CLAUDE_HOME = Path.home() / ".claude"
USAGE_CACHE = CLAUDE_HOME / ".usage-cache.json"
HEALTH_INTERVAL = 1800  # 30 minutes
CB_PROBE_INTERVAL = 300  # circuit breaker probe: 5 minutes


def _log_fallback(sid, from_level, from_label, to_level, to_label, provider, reason):
    """Record a fallback event to SQLite."""
    try:
        conn = db_connect()
        conn.execute(
            "INSERT INTO fallback_log (session_id, from_level, from_label, to_level, to_label, provider, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, from_level, from_label, to_level, to_label, provider, reason),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------
_circuit_breaker = {}  # {provider_name: {"banned": bool, "reason": str, "banned_at": float}}


def cb_ban(provider_name, reason):
    """Ban a provider — skip it in the fallback chain."""
    _circuit_breaker[provider_name] = {
        "banned": True,
        "reason": reason,
        "banned_at": time.time(),
    }
    print(f"[msc] Circuit breaker BAN: {provider_name} — {reason}", flush=True)


def cb_unban(provider_name):
    """Unban a provider — it's back in the fallback chain."""
    if provider_name in _circuit_breaker:
        _circuit_breaker[provider_name]["banned"] = False
        print(f"[msc] Circuit breaker UNBAN: {provider_name}", flush=True)


def cb_is_banned(provider_name):
    """Check if a provider is currently banned."""
    state = _circuit_breaker.get(provider_name)
    return bool(state and state.get("banned"))


def cb_status():
    """Return circuit breaker status for all providers."""
    return {name: dict(state) for name, state in _circuit_breaker.items()}


# Error codes that indicate quota exhaustion → immediate ban
_CB_BAN_CODES = {"1304", "1308", "1309", "1310"}


def _cb_probe_loop():
    """Background thread: probe banned providers every CB_PROBE_INTERVAL."""
    time.sleep(30)  # initial delay
    while True:
        time.sleep(CB_PROBE_INTERVAL)
        try:
            banned_providers = [name for name, state in _circuit_breaker.items()
                                if state.get("banned")]
            if not banned_providers or not Handler.config or not Handler.clients:
                continue

            for provider_name in banned_providers:
                provider_cfg = Handler.config.get("providers", {}).get(provider_name)
                if not provider_cfg:
                    continue
                # Find a model for this provider to test with
                test_model = ""
                for lvl_info in Handler.route_levels.values():
                    if lvl_info.get("provider") == provider_name:
                        test_model = lvl_info["model"]
                        break
                if not test_model:
                    continue

                result = _check_custom_api_health(
                    provider_name, provider_cfg, test_model, Handler.clients
                )
                if result["status"] == "ok":
                    cb_unban(provider_name)
                else:
                    print(f"[msc] CB probe: {provider_name} still down — {result.get('error', '')}", flush=True)

            # Also probe anthropic if banned
            if cb_is_banned("anthropic"):
                result = _check_anthropic_health()
                if result["status"] == "ok":
                    cb_unban("anthropic")
                else:
                    print(f"[msc] CB probe: anthropic still down — {result.get('error', '')}", flush=True)

        except Exception as e:
            print(f"[msc] CB probe error: {e}", flush=True)


def _read_claude_credentials():
    """Read OAuth credentials from macOS keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except Exception:
        pass
    return None


def _check_anthropic_health():
    """Check Anthropic availability via OAuth token + usage API."""
    import httpx
    result = {"status": "unknown", "error": "", "details": {}}

    # 1. Read OAuth token from keychain
    creds = _read_claude_credentials()
    if not creds:
        result["status"] = "unknown"
        result["error"] = "无法读取 Claude 凭据"
        return result

    oauth = creds.get("claudeAiOauth", {})
    token = oauth.get("accessToken", "")
    if not token:
        result["status"] = "fail"
        result["error"] = "OAuth Token 不存在，请先 claude login"
        return result

    # 2. Check token expiry
    expires_at = oauth.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)
    if expires_at and now_ms > expires_at:
        result["status"] = "fail"
        result["error"] = "OAuth Token 已过期，请重新登录"
        return result

    remaining_hours = (expires_at - now_ms) / 1000 / 3600 if expires_at else 0
    result["details"]["token_expires_in_hours"] = round(remaining_hours, 1)
    result["details"]["subscription"] = oauth.get("subscriptionType", "unknown")

    # 3. Call Anthropic usage API directly
    try:
        proxy_url = Handler.config.get("proxy", "") if Handler.config else ""
        client_kwargs = {"timeout": 10}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        with httpx.Client(**client_kwargs) as client:
            resp = client.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                },
            )

        if resp.status_code == 401:
            result["status"] = "fail"
            result["error"] = "认证失败，请重新 claude login"
            return result

        if resp.status_code != 200:
            result["status"] = "unknown"
            result["error"] = f"用量 API 返回 {resp.status_code}"
            return result

        data = resp.json()
        five_hour = int(data.get("five_hour", {}).get("utilization", 0))
        seven_day = int(data.get("seven_day", {}).get("utilization", 0))
        seven_day_sonnet_raw = data.get("seven_day_sonnet") or {}
        seven_day_sonnet = int(seven_day_sonnet_raw.get("utilization", 0)) if seven_day_sonnet_raw else None

        result["details"]["five_hour_usage"] = five_hour
        result["details"]["seven_day_usage"] = seven_day
        result["details"]["five_hour_reset"] = data.get("five_hour", {}).get("resets_at", "")
        result["details"]["seven_day_reset"] = data.get("seven_day", {}).get("resets_at", "")
        if seven_day_sonnet is not None:
            result["details"]["seven_day_sonnet_usage"] = seven_day_sonnet
            result["details"]["seven_day_sonnet_reset"] = seven_day_sonnet_raw.get("resets_at", "")

        # Also update the local cache file for statusline-command.sh
        try:
            cache_data = {
                "timestamp": int(time.time()),
                "five_hour": str(five_hour),
                "seven_day": str(seven_day),
                "five_hour_reset_iso": result["details"]["five_hour_reset"],
                "seven_day_reset_iso": result["details"]["seven_day_reset"],
            }
            if seven_day_sonnet is not None:
                cache_data["seven_day_sonnet"] = str(seven_day_sonnet)
                cache_data["seven_day_sonnet_reset_iso"] = result["details"]["seven_day_sonnet_reset"]
            USAGE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            with open(USAGE_CACHE, "w") as f:
                json.dump(cache_data, f)
        except Exception:
            pass

        if five_hour >= 100 and seven_day >= 100:
            result["status"] = "fail"
            result["error"] = f"5h({five_hour}%) 和 7d({seven_day}%) 额度均已用完"
        elif five_hour >= 100:
            result["status"] = "warn"
            result["error"] = f"5h 额度已用完 ({five_hour}%)"
        elif seven_day >= 100:
            result["status"] = "warn"
            result["error"] = f"7d 额度已用完 ({seven_day}%)"
        elif seven_day_sonnet is not None and seven_day_sonnet >= 100:
            result["status"] = "warn"
            result["error"] = f"Sonnet 7d 额度已用完 ({seven_day_sonnet}%)"
        else:
            result["status"] = "ok"

    except Exception as e:
        result["status"] = "unknown"
        result["error"] = f"用量检查失败: {str(e)[:80]}"

    return result


def _fetch_anthropic_quota():
    """Fetch real-time Anthropic quota with reset times.
    Returns dict with usage percentages and human-readable reset times, or None on failure.
    """
    creds = _read_claude_credentials()
    if not creds:
        return None
    oauth = creds.get("claudeAiOauth", {})
    token = oauth.get("accessToken", "")
    if not token:
        return None

    try:
        proxy_url = Handler.config.get("proxy", "") if Handler.config else ""
        client_kwargs = {"timeout": 10}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        with httpx.Client(**client_kwargs) as client:
            resp = client.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                },
            )

        if resp.status_code != 200:
            return None

        data = resp.json()
        quota = {"subscription": oauth.get("subscriptionType", "unknown"), "tiers": []}

        # Parse each usage tier
        tiers = [
            ("5h", data.get("five_hour", {})),
            ("7d", data.get("seven_day", {})),
            ("7d Sonnet", data.get("seven_day_sonnet") or {}),
        ]
        for label, tier_data in tiers:
            if not tier_data:
                continue
            utilization = int(tier_data.get("utilization", 0))
            resets_at = tier_data.get("resets_at", "")
            remaining = max(0, 100 - utilization)
            quota["tiers"].append({
                "label": label,
                "utilization": utilization,
                "remaining": remaining,
                "resets_at": resets_at,
                "exhausted": utilization >= 100,
            })

        return quota
    except Exception:
        return None


def _check_custom_api_health(provider_name, provider_cfg, model, clients):
    """Check custom API (GLM etc) by sending a minimal request."""
    result = {"status": "unknown", "error": "", "details": {}}
    url = provider_cfg.get("url", "")
    key = provider_cfg.get("key", "")
    if not url or not key:
        result["status"] = "fail"
        result["error"] = "缺少 API 地址或 Key"
        return result

    try:
        client = clients.get(provider_name)
        if not client:
            result["status"] = "fail"
            result["error"] = "HTTP client 未初始化"
            return result

        headers = {
            "content-type": "application/json",
            "x-api-key": key,
            "authorization": f"Bearer {key}",
            "anthropic-version": "2023-06-01",
        }
        body = json.dumps({
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        })
        resp = client.post(url, headers=headers, content=body, timeout=10)
        if resp.status_code == 200:
            result["status"] = "ok"
        else:
            result["status"] = "fail"
            try:
                err = resp.json().get("error", {}).get("message", resp.text[:100])
            except Exception:
                err = f"HTTP {resp.status_code}"
            result["error"] = str(err)
    except Exception as e:
        result["status"] = "fail"
        result["error"] = str(e)[:100]

    return result


def run_health_check(config, route_levels, clients):
    """Run health check on all configured models."""
    import datetime
    results = {}
    for level_str, level_info in sorted(route_levels.items()):
        level_int = int(level_str) if isinstance(level_str, str) else level_str
        provider_name = level_info.get("provider", "")
        model = level_info.get("model", "")
        label = level_info.get("label", "")

        if provider_name == "anthropic" or level_info.get("passthrough_auth"):
            check = _check_anthropic_health()
        else:
            provider_cfg = config.get("providers", {}).get(provider_name, {})
            check = _check_custom_api_health(provider_name, provider_cfg, model, clients)

        check["checked_at"] = datetime.datetime.now().isoformat()
        check["model"] = model
        check["label"] = label
        check["provider"] = provider_name
        results[level_int] = check
        status_icon = {"ok": "✓", "fail": "✗", "warn": "⚠", "unknown": "?"}.get(check["status"], "?")
        print(f"[msc] Health {status_icon} Level {level_int} ({label}): {check['status']} {check.get('error','')}", flush=True)

    return results


def _health_check_loop():
    """Background thread: run health check every HEALTH_INTERVAL seconds."""
    time.sleep(10)  # initial delay
    while True:
        try:
            if Handler.config and Handler.route_levels and Handler.clients:
                Handler._model_health = run_health_check(
                    Handler.config, Handler.route_levels, Handler.clients
                )
        except Exception as e:
            print(f"[msc] Health check error: {e}", flush=True)
        time.sleep(HEALTH_INTERVAL)


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------
def get_fallback_order(current_level, max_level):
    """Down first, then up. E.g., current=2, max=3 → [2, 1, 3]"""
    order = [current_level]
    for l in range(current_level - 1, 0, -1):
        order.append(l)
    for l in range(current_level + 1, max_level + 1):
        order.append(l)
    return order


# ---------------------------------------------------------------------------
# Error Classification
# ---------------------------------------------------------------------------
class ProxyFallbackError(Exception):
    """应 fallback 到下一个 provider 的错误"""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class ProxyFatalError(Exception):
    """不可 fallback 的错误，响应已发送给客户端"""
    pass


def _extract_business_code(resp_body, code_path):
    """从 JSON 响应体提取业务错误码，如 'error.code' -> resp['error']['code']"""
    try:
        data = json.loads(resp_body) if isinstance(resp_body, bytes) else resp_body
        current = data
        for key in code_path.split("."):
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        return str(current) if current is not None else None
    except Exception:
        return None


def classify_error(status_code, resp_body, provider_name, fallback_cfg):
    """分类错误，返回 (should_fallback, reason)。

    判断优先级：per-provider 业务码 > HTTP 状态码。

    Returns:
        (True, reason)  — 应该 fallback 到下一个 provider
        (False, reason) — 不可 fallback，直接返回给客户端
    """
    rules = fallback_cfg.get("error_rules", {})
    default_rules = rules.get("_default", {})
    provider_rules = rules.get(provider_name, {})

    retriable_http = set(provider_rules.get("retriable_http",
                            default_rules.get("retriable_http", [429, 500, 502, 503, 529])))
    fatal_http = set(provider_rules.get("fatal_http",
                       default_rules.get("fatal_http", [400, 401, 403, 404])))

    # 1. 优先检查 per-provider 业务错误码（可覆盖 HTTP 级分类）
    code_path = provider_rules.get("business_code_path",
                  default_rules.get("business_code_path", ""))
    if code_path:
        biz_code = _extract_business_code(resp_body, code_path)
        if biz_code:
            provider_fatal = set(provider_rules.get("fatal_codes", []))
            provider_retriable = set(provider_rules.get("retriable_codes", []))

            if biz_code in provider_fatal:
                # Quota exhaustion codes → proactive ban
                if biz_code in _CB_BAN_CODES:
                    cb_ban(provider_name, f"biz code {biz_code}")
                return False, f"biz code {biz_code} (fatal)"
            if biz_code in provider_retriable:
                return True, f"biz code {biz_code} (retriable)"
            # 业务码不在任何列表中 — 按保守策略：5xx retriable，其余 fatal
            if status_code >= 500:
                return True, f"HTTP {status_code} biz {biz_code} (unknown, server error)"
            return False, f"HTTP {status_code} biz {biz_code} (unknown, fatal)"

    # 2. 无业务码时，按 HTTP 状态码判断
    if status_code in retriable_http:
        return True, f"HTTP {status_code} (retriable)"
    if status_code in fatal_http:
        return False, f"HTTP {status_code} (fatal)"

    # 3. 未知状态码：5xx 可 fallback，4xx 不可
    if status_code >= 500:
        return True, f"HTTP {status_code} (server error, retriable)"
    if status_code >= 400:
        return False, f"HTTP {status_code} (client error, fatal)"

    return False, f"HTTP {status_code} (unexpected)"


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    config = None
    route_levels = None
    clients = None  # {provider_name: httpx.Client}
    active_session = None
    _model_health = {}  # {level_int: {"status": "ok"|"fail"|"unknown", "error": "", "checked_at": ""}}
    _health_check_running = False

    def log_message(self, fmt, *args):
        pass

    # ---- GET endpoints ----

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        sid = params.get("session", [""])[0] or Handler.active_session or ""

        if path == "/":
            self._json({"status": "ok"})

        elif path == "/register" and sid:
            self._handle_register(sid)

        elif path == "/think-level" and sid:
            level_str = params.get("level", [""])[0]
            self._handle_think_level(sid, level_str)

        elif path == "/use-min" and sid:
            min_level = str(min(int(k) for k in self.route_levels.keys()))
            self._handle_think_level(sid, min_level)

        elif path == "/use-max" and sid:
            max_level = str(max(int(k) for k in self.route_levels.keys()))
            self._handle_think_level(sid, max_level)

        elif path == "/status" and sid:
            self._handle_status(sid)

        elif path == "/config":
            self._handle_config_get(params)

        elif path == "/routing-prompt":
            self._json({"prompt": generate_routing_prompt(self.config)})

        elif path == "/sessions":
            self._handle_sessions()

        elif path == "/ui":
            self._handle_ui()

        elif path.startswith("/cot/"):
            self._handle_cot_static(path)

        elif path == "/api/cot/sessions":
            self._handle_cot_sessions(params)

        elif path == "/api/cot/projects":
            self._handle_cot_projects()

        elif path.startswith("/api/cot/session/") and path.endswith("/messages"):
            cot_sid = path[len("/api/cot/session/"):-len("/messages")]
            self._handle_cot_messages(cot_sid, params)

        elif path.startswith("/api/cot/session/") and path.endswith("/info"):
            cot_sid = path[len("/api/cot/session/"):-len("/info")]
            self._handle_cot_info(cot_sid)

        elif path == "/health-check":
            # Manual trigger health check
            if Handler._health_check_running:
                self._json({"status": "already running"})
            else:
                Handler._health_check_running = True
                try:
                    Handler._model_health = run_health_check(
                        self.config, self.route_levels, self.clients
                    )
                finally:
                    Handler._health_check_running = False
                self._json(Handler._model_health)

        elif path == "/health-status":
            self._json(Handler._model_health)

        elif path == "/api/claude-account":
            self._handle_claude_account()

        # CUI-compatible API endpoints
        elif path == "/api/system/auth-status":
            self._json({"authRequired": False})

        elif path == "/api/conversations":
            self._handle_cui_conversations(params)

        elif path.startswith("/api/conversations/"):
            cui_sid = path[len("/api/conversations/"):]
            self._handle_cui_conversation_details(cui_sid)

        elif path == "/api/subscriptions/subscribe" or path == "/api/subscriptions/unsubscribe":
            self._json({"success": True})

        elif path == "/api/quota":
            self._handle_quota()

        elif path == "/api/circuit-breaker":
            self._json(cb_status())

        elif path == "/api/fallback-log":
            self._handle_fallback_log(params)

        else:
            self._json({"status": "ok"})

    def _handle_register(self, sid):
        Handler.active_session = sid
        conn = db_connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, level) VALUES (?, ?)",
                (sid, self.config["default_level"]),
            )
            conn.commit()
            row = conn.execute(
                "SELECT level FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
            level = row["level"] if row else self.config["default_level"]
        finally:
            conn.close()
        level_info = self.route_levels.get(level, {})
        print(f"[msc] Register: {sid[:8]}… → Level {level} ({level_info.get('label', '?')})", flush=True)
        # Index session immediately (async, don't block response)
        threading.Thread(target=lambda: _index_session(sid), daemon=True).start()
        self._json({"session_id": sid, "level": level, "name": level_info.get("name", ""), "label": level_info.get("label", "")})

    def _handle_think_level(self, sid, level_str):
        Handler.active_session = sid
        try:
            level = int(level_str)
        except (ValueError, TypeError):
            self._json({"error": "invalid level"}, status=400)
            return
        if level not in self.route_levels:
            self._json({"error": f"unknown level {level}"}, status=400)
            return
        conn = db_connect()
        try:
            conn.execute(
                "INSERT INTO sessions (session_id, level) VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET level = ?, updated_at = datetime('now')",
                (sid, level, level),
            )
            conn.commit()
        finally:
            conn.close()
        level_info = self.route_levels[level]
        print(f"[msc] Switch: {sid[:8]}… → Level {level} ({level_info['label']})", flush=True)
        self._json({"session_id": sid, "level": level, "name": level_info["name"], "label": level_info["label"]})

    def _handle_status(self, sid):
        conn = db_connect()
        try:
            row = conn.execute(
                "SELECT level FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
        finally:
            conn.close()
        level = row["level"] if row else self.config["default_level"]
        level_info = self.route_levels.get(level, {})
        self._json({"session_id": sid, "level": level, "name": level_info.get("name", ""), "label": level_info.get("label", "")})

    def _handle_sessions(self):
        conn = db_connect()
        try:
            rows = conn.execute(
                "SELECT s.session_id, s.level, s.created_at, s.updated_at FROM sessions s ORDER BY s.updated_at DESC LIMIT 50"
            ).fetchall()
            result = []
            for row in rows:
                sid, level, created, updated = row
                usage_rows = conn.execute(
                    "SELECT provider, model_name, label, "
                    "SUM(input_tokens) as inp, SUM(output_tokens) as out, "
                    "SUM(cache_read_tokens) as cache_read, SUM(cache_create_tokens) as cache_create "
                    "FROM usage WHERE session_id = ? GROUP BY provider, model_name",
                    (sid,),
                ).fetchall()
                usage = [{
                    "provider": u[0], "model_name": u[1] or "", "label": u[2] or u[0],
                    "input_tokens": u[3], "output_tokens": u[4],
                    "cache_read_tokens": u[5] or 0, "cache_create_tokens": u[6] or 0,
                } for u in usage_rows]
                level_info = Handler.route_levels.get(level, {})
                result.append({
                    "session_id": sid, "level": level,
                    "name": level_info.get("name", "?"), "label": level_info.get("label", "?"),
                    "created_at": created, "updated_at": updated, "usage": usage,
                })
            self._json(result)
        finally:
            conn.close()

    def _handle_config_get(self, params=None):
        cfg = load_config()
        show_keys = params and params.get("show_keys", [""])[0] == "1"
        if not show_keys:
            cfg = json.loads(json.dumps(cfg))
            for pname, prov in cfg.get("providers", {}).items():
                if "key" in prov:
                    prov["key"] = mask_key(prov["key"])
        self._json(cfg)

    def _handle_ui(self):
        try:
            content = DASHBOARD_PATH.read_text()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode())
        except FileNotFoundError:
            self._json({"error": "dashboard.html not found"}, status=404)

    def _handle_cot_static(self, path):
        """Serve CUI static files from ui/cot/ directory."""
        import mimetypes
        # /cot/ → ui/cot/index.html, /cot/assets/x.js → ui/cot/assets/x.js
        rel = path[len("/cot/"):] or "index.html"
        if not rel or rel.endswith("/"):
            rel += "index.html"
        file_path = Path(__file__).resolve().parent / "ui" / "cot" / rel
        # Security: prevent path traversal
        try:
            file_path = file_path.resolve()
            cot_dir = (Path(__file__).resolve().parent / "ui" / "cot").resolve()
            if not str(file_path).startswith(str(cot_dir)):
                self.send_response(403)
                self.end_headers()
                return
        except Exception:
            self.send_response(400)
            self.end_headers()
            return
        if not file_path.exists():
            # SPA fallback: serve index.html for any non-file path (e.g. /cot/c/{id})
            file_path = cot_dir / "index.html"
            if not file_path.exists():
                self.send_response(404)
                self.end_headers()
                return
        mime, _ = mimetypes.guess_type(str(file_path))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    # ---- CoT API endpoints ----

    def _handle_cot_projects(self):
        """List all project directories under ~/.claude/projects/ as a tree.
        Only count sessions registered in the CR sessions table."""
        projects_dir = Path.home() / ".claude" / "projects"
        if not projects_dir.exists():
            self._json([])
            return

        # Get registered session IDs
        conn = db_connect()
        try:
            rows = conn.execute("SELECT session_id FROM sessions").fetchall()
            registered = {r["session_id"] for r in rows}
        finally:
            conn.close()

        result = []
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir():
                continue
            # Convert dir name to readable path: -Users-limuyu-work-MuYu -> ~/work/MuYu
            name = d.name
            readable = name.replace("-", "/")
            if readable.startswith("/Users/"):
                parts = readable.split("/")
                # /Users/username/rest -> ~/rest
                readable = "~/" + "/".join(parts[3:]) if len(parts) > 3 else "~"

            # Count only registered sessions
            session_count = len([
                f for f in d.iterdir()
                if f.suffix == '.jsonl' and not f.name.startswith('agent-')
                and f.stem in registered
            ])

            if session_count == 0:
                continue

            result.append({
                "dir_name": name,
                "path": readable,
                "session_count": session_count,
            })

        self._json(result)

    def _handle_cot_sessions(self, params):
        q = params.get("q", [""])[0].strip()
        project = params.get("project", [""])[0].strip()

        # Get registered session IDs (only show CR-launched sessions)
        conn = db_connect()
        try:
            rows = conn.execute("SELECT session_id FROM sessions").fetchall()
            registered = {r["session_id"] for r in rows}
        finally:
            conn.close()

        # If project is specified, scan that directory directly for jsonl files
        if project:
            projects_dir = Path.home() / ".claude" / "projects"
            proj_dir = projects_dir / project
            if not proj_dir.exists() or not proj_dir.is_dir():
                self._json([])
                return

            result = []
            for f in sorted(proj_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if not f.suffix == '.jsonl' or f.name.startswith('agent-'):
                    continue
                session_id = f.stem

                # Only show sessions registered through CR
                if session_id not in registered:
                    continue

                file_size = f.stat().st_size

                # Try to get metadata from index first
                conn = db_connect()
                try:
                    row = conn.execute(
                        "SELECT first_query, last_timestamp FROM session_index WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                finally:
                    conn.close()

                if row:
                    first_query = row["first_query"]
                    last_timestamp = row["last_timestamp"]
                else:
                    # Fallback: extract from file
                    first_query = _extract_first_query(str(f))
                    last_timestamp = _extract_last_timestamp(str(f))

                entry = {
                    "session_id": session_id,
                    "project_path": _dir_to_project_path(project),
                    "first_query": first_query,
                    "last_timestamp": last_timestamp,
                    "file_size": file_size,
                }
                if q:
                    like = q.lower()
                    if like not in (first_query or "").lower() and like not in session_id.lower():
                        continue
                result.append(entry)

            self._json(result[:100])
            return

        conn = db_connect()
        try:
            if q:
                like = f"%{q}%"
                rows = conn.execute(
                    """SELECT session_id, project_path, first_query, last_timestamp, file_size
                       FROM session_index
                       WHERE session_id LIKE ? OR first_query LIKE ? OR project_path LIKE ?
                       ORDER BY last_timestamp DESC LIMIT 100""",
                    (like, like, like),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT session_id, project_path, first_query, last_timestamp, file_size
                       FROM session_index
                       ORDER BY last_timestamp DESC LIMIT 100""",
                ).fetchall()
            result = [
                {
                    "session_id": r["session_id"],
                    "project_path": r["project_path"],
                    "first_query": r["first_query"],
                    "last_timestamp": r["last_timestamp"],
                    "file_size": r["file_size"],
                }
                for r in rows
            ]
            self._json(result)
        finally:
            conn.close()

    def _handle_cot_messages(self, session_id, params):
        conn = db_connect()
        try:
            row = conn.execute(
                "SELECT file_path FROM session_index WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row or not row["file_path"]:
            self._json({"error": "session not found"}, status=404)
            return

        filepath = row["file_path"]
        if not os.path.exists(filepath):
            self._json({"error": "jsonl file not found"}, status=404)
            return

        limit = int(params.get("limit", ["20"])[0])
        offset = int(params.get("offset", ["0"])[0])
        direction = params.get("direction", ["tail"])[0]
        before_line = params.get("before_line", [None])[0]

        if before_line:
            messages = read_jsonl_before(filepath, int(before_line), limit=limit)
        elif direction == "tail":
            messages = read_jsonl_tail(filepath, limit=limit)
        else:
            messages = read_jsonl_head(filepath, offset=offset, limit=limit)

        # Clean up messages for response: extract relevant fields
        result = []
        for msg in messages:
            entry = {
                "type": msg.get("type", ""),
                "timestamp": msg.get("timestamp", ""),
                "line_number": msg.pop("_line_number", 0),
            }
            message = msg.get("message", {})
            if isinstance(message, dict):
                entry["content"] = message.get("content", "")
                entry["role"] = message.get("role", "")
                entry["model"] = message.get("model", "")
            else:
                entry["content"] = ""
                entry["role"] = ""
                entry["model"] = ""
            result.append(entry)

        self._json(result)

    def _handle_cot_info(self, session_id):
        conn = db_connect()
        try:
            row = conn.execute(
                "SELECT session_id, project_path, first_query, last_timestamp, file_path, file_size FROM session_index WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            self._json({"error": "session not found"}, status=404)
            return

        msg_count = 0
        if row["file_path"] and os.path.exists(row["file_path"]):
            msg_count = count_messages_in_file(row["file_path"])

        self._json({
            "session_id": row["session_id"],
            "project_path": row["project_path"],
            "first_query": row["first_query"],
            "last_timestamp": row["last_timestamp"],
            "file_size": row["file_size"],
            "message_count": msg_count,
        })

    def _handle_claude_account(self):
        """Return Claude account info + usage for dashboard display."""
        result = {"logged_in": False}
        creds = _read_claude_credentials()
        if not creds:
            self._json(result)
            return
        oauth = creds.get("claudeAiOauth", {})
        token = oauth.get("accessToken", "")
        if not token:
            self._json(result)
            return

        expires_at = oauth.get("expiresAt", 0)
        now_ms = int(time.time() * 1000)
        result["logged_in"] = True
        result["subscription"] = oauth.get("subscriptionType", "unknown")
        result["token_expires_in_hours"] = round((expires_at - now_ms) / 1000 / 3600, 1) if expires_at else 0
        result["token_expired"] = now_ms > expires_at if expires_at else False

        # Get email from claude auth status
        try:
            out = subprocess.run(
                ["claude", "auth", "status"], capture_output=True, text=True, timeout=5
            )
            if out.returncode == 0:
                import re as _re
                info = json.loads(out.stdout)
                result["email"] = info.get("email", "")
                result["org_name"] = info.get("orgName", "")
        except Exception:
            pass

        # Get usage from health check cache
        for level_int, health in Handler._model_health.items():
            if health.get("provider") == "anthropic" and health.get("details"):
                result["usage"] = health["details"]
                break

        self._json(result)

    def _handle_quota(self):
        """Return quota status for all providers with reset times."""
        result = {"anthropic": None, "providers": {}}

        # --- Anthropic (Claude subscription) ---
        anthropic_quota = _fetch_anthropic_quota()
        if anthropic_quota:
            result["anthropic"] = anthropic_quota

        # --- Custom providers (from health check cache) ---
        for level_int, health in Handler._model_health.items():
            provider = health.get("provider", "")
            if provider == "anthropic":
                continue
            if provider not in result["providers"]:
                result["providers"][provider] = {
                    "status": health.get("status", "unknown"),
                    "error": health.get("error", ""),
                    "model": health.get("model", ""),
                    "checked_at": health.get("checked_at", ""),
                }

        self._json(result)

    def _handle_fallback_log(self, params):
        """Return recent fallback events."""
        limit = int(params.get("limit", ["50"])[0])
        session_id = params.get("session", [""])[0]
        conn = db_connect()
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM fallback_log WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM fallback_log ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            result = [{
                "id": r["id"],
                "session_id": r["session_id"],
                "from_level": r["from_level"],
                "from_label": r["from_label"],
                "to_level": r["to_level"],
                "to_label": r["to_label"],
                "provider": r["provider"],
                "reason": r["reason"],
                "timestamp": r["timestamp"],
            } for r in rows]
        finally:
            conn.close()
        self._json(result)

    # ---- CUI-compatible API endpoints ----

    def _handle_cui_conversations(self, params):
        """Return session list in CUI-compatible format."""
        conn = db_connect()
        try:
            rows = conn.execute(
                "SELECT session_id, project_path, first_query, last_timestamp FROM session_index ORDER BY last_timestamp DESC LIMIT 200"
            ).fetchall()
        finally:
            conn.close()
        conversations = []
        for r in rows:
            conversations.append({
                "sessionId": r["session_id"],
                "summary": r["first_query"] or "",
                "projectPath": r["project_path"] or "",
                "createdAt": r["last_timestamp"] or "",
                "updatedAt": r["last_timestamp"] or "",
                "status": "completed",
                "streamingId": None,
                "sessionInfo": {
                    "custom_name": "",
                    "archived": False,
                    "pinned": False,
                },
                "toolMetrics": None,
            })
        self._json({"conversations": conversations, "total": len(conversations)})

    def _handle_cui_conversation_details(self, session_id):
        """Read full JSONL and return CUI-compatible ConversationDetailsResponse."""
        conn = db_connect()
        try:
            row = conn.execute(
                "SELECT file_path, project_path, first_query FROM session_index WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row or not row["file_path"]:
            self._json({"error": "session not found"}, status=404)
            return

        filepath = row["file_path"]
        if not os.path.exists(filepath):
            self._json({"error": "jsonl file not found"}, status=404)
            return

        # Parse all JSONL entries (CUI needs raw message objects)
        messages = []
        model = ""
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    etype = entry.get("type", "")
                    if etype not in ("user", "assistant"):
                        continue
                    msg = entry.get("message", {})
                    if not msg:
                        continue
                    # Track model from assistant messages
                    if etype == "assistant" and isinstance(msg, dict):
                        m = msg.get("model", "")
                        if m:
                            model = m
                    messages.append({
                        "uuid": entry.get("uuid", ""),
                        "type": etype,
                        "message": msg,
                        "timestamp": entry.get("timestamp", ""),
                        "sessionId": session_id,
                        "parentUuid": entry.get("parentUuid"),
                        "isSidechain": entry.get("isSidechain", False),
                        "cwd": entry.get("cwd", ""),
                    })
        except Exception:
            self._json({"error": "failed to read session"}, status=500)
            return

        self._json({
            "messages": messages,
            "summary": row["first_query"] or "",
            "projectPath": row["project_path"] or "",
            "metadata": {
                "totalDuration": 0,
                "model": model,
            },
        })

    # ---- POST endpoints ----

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        if path == "/config":
            self._handle_config_post(raw)
            return

        # CUI subscription endpoints (no-op stubs)
        if path.startswith("/api/subscriptions/"):
            self._json({"success": True})
            return

        if not path.startswith("/v1/messages"):
            self._json({"error": "not found"}, status=404)
            return

        self._handle_proxy(raw)

    def _handle_config_post(self, raw):
        try:
            new_cfg = json.loads(raw)
            # Save history before overwriting
            history_path = Path(os.path.expanduser("~/.msc/config-history.jsonl"))
            try:
                import datetime
                entry = {"timestamp": datetime.datetime.now().isoformat(), "config": new_cfg}
                with open(history_path, "a") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass
            save_config(new_cfg)
            # Reload
            Handler.config = load_config()
            Handler.route_levels = build_route_levels(Handler.config)
            _rebuild_clients()
            write_routing_prompt(Handler.config)
            self._json({"status": "config updated"})
            print("[msc] Config updated and reloaded", flush=True)
        except Exception as e:
            self._json({"error": str(e)}, status=500)

    def _handle_proxy(self, raw):
        body = json.loads(raw)

        # Resolve session level
        sid = Handler.active_session
        level = self.config["default_level"]
        if sid:
            conn = db_connect()
            try:
                row = conn.execute(
                    "SELECT level FROM sessions WHERE session_id = ?", (sid,)
                ).fetchone()
                if row:
                    level = row["level"]
            finally:
                conn.close()

        max_level = max(self.route_levels.keys())
        # If session's level no longer exists, clamp to nearest valid level
        if level not in self.route_levels:
            valid = sorted(self.route_levels.keys())
            level = min(valid, key=lambda l: abs(l - level))
        fallback_order = [l for l in get_fallback_order(level, max_level) if l in self.route_levels]

        last_error = None
        tried_levels = []  # track failed attempts for logging
        for try_level in fallback_order:
            level_info = self.route_levels[try_level]
            provider_name = level_info["provider"]

            # Circuit breaker: skip banned providers
            if cb_is_banned(provider_name):
                reason = cb_status().get(provider_name, {}).get('reason', '')
                print(f"[msc] CB skip: L{try_level} ({level_info['label']}) — banned: {reason}", flush=True)
                tried_levels.append((try_level, level_info['label'], provider_name, f"CB banned: {reason}"))
                continue

            provider_cfg = self.config["providers"][provider_name]
            client = self.clients[provider_name]

            try:
                self._do_proxy_request(body, try_level, level_info, provider_name, provider_cfg, client, sid)
                # Success — log any fallbacks that occurred
                for (fl, flbl, fp, fr) in tried_levels:
                    _log_fallback(sid, fl, flbl, try_level, level_info['label'], fp, fr)
                return  # success
            except ProxyFallbackError as e:
                last_error = e
                tried_levels.append((try_level, level_info['label'], provider_name, e.reason))
                print(f"[msc] Fallback: L{try_level} ({level_info['label']}) → {e.reason}", flush=True)
                # Ban Anthropic on persistent 429 (quota exhaustion)
                if provider_name == "anthropic" and "429" in str(e.reason):
                    quota = _fetch_anthropic_quota()
                    if quota:
                        for tier in quota.get("tiers", []):
                            if tier.get("exhausted"):
                                cb_ban("anthropic", f"quota exhausted: {tier['label']} {tier['utilization']}%")
                                break
                continue  # try next level
            except ProxyFatalError:
                return  # response already sent to client

        # All fallbacks exhausted
        print(f"[msc] All fallbacks exhausted: {last_error}", flush=True)
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": str(last_error)}).encode())

    def _do_proxy_request(self, body, level, level_info, provider_name, provider_cfg, client, sid):
        is_stream = body.get("stream", False)
        is_glm = provider_name == "glm"
        url = provider_cfg["url"]

        # Build request body and headers
        req_body = json.loads(json.dumps(body))  # deep copy

        if is_glm:
            # Strip auth, inject provider key, rewrite model
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length", "x-api-key", "authorization")}
            # GLM uses x-api-key (same as Anthropic format)
            headers["x-api-key"] = provider_cfg["key"]
            # Also set authorization for providers that need Bearer token
            headers["authorization"] = "Bearer " + provider_cfg["key"]
            req_body["model"] = level_info["model"]
        else:
            # Anthropic: passthrough all headers (OAuth compatible), fix signatures
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length")}
            # Override model to match the configured level
            req_body["model"] = level_info["model"]
            n = fix_signatures(req_body.get("messages", []))
            if n:
                print(f"[msc] Fixed {n} signature(s)", flush=True)
            if not is_glm:
                url = provider_cfg["url"] + self.path

        content = json.dumps(req_body)
        print(f"[msc] → Level {level} ({level_info['label']})", flush=True)

        try:
            if is_stream:
                self._stream_response(client, url, headers, content, is_glm, sid, level, provider_name)
            else:
                self._non_stream_response(client, url, headers, content, is_glm, sid, level, provider_name)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError, httpx.WriteError, OSError) as e:
            # Network-level errors always trigger fallback
            raise ProxyFallbackError(f"network: {type(e).__name__}: {e}")

    def _stream_response(self, client, url, headers, content, is_glm, sid, level, provider_name):
        with client.stream("POST", url, headers=headers, content=content) as resp:
            if resp.status_code >= 400:
                # Error response — read body and decide whether to fallback
                error_body = resp.read()
                fallback_cfg = self.config.get("fallback", {})
                should_fallback, reason = classify_error(
                    resp.status_code, error_body, provider_name, fallback_cfg
                )
                if should_fallback:
                    raise ProxyFallbackError(reason)
                # Not retriable — send error to client
                self.send_response(resp.status_code)
                for k, v in resp.headers.multi_items():
                    if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(error_body)
                raise ProxyFatalError(reason)

            self.send_response(resp.status_code)
            for k, v in resp.headers.multi_items():
                if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                    self.send_header(k, v)
            self.end_headers()

            patcher = StreamSignaturePatcher() if is_glm else None
            collected_lines = []
            for chunk in resp.iter_raw():
                if patcher:
                    chunk = patcher.feed(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
                # Collect for usage parsing
                try:
                    collected_lines.append(chunk.decode())
                except Exception:
                    pass

            # Parse usage from collected stream data
            self._track_stream_usage(collected_lines, sid, level, provider_name)

    def _non_stream_response(self, client, url, headers, content, is_glm, sid, level, provider_name):
        resp = client.post(url, headers=headers, content=content)

        if resp.status_code >= 400:
            # Error response — decide whether to fallback
            fallback_cfg = self.config.get("fallback", {})
            should_fallback, reason = classify_error(
                resp.status_code, resp.content, provider_name, fallback_cfg
            )
            if should_fallback:
                raise ProxyFallbackError(reason)
            # Not retriable — send error to client
            resp_body = resp.content
            if is_glm:
                resp_body = patch_json_signatures(resp_body)
            self.send_response(resp.status_code)
            self.send_header("Content-Type", resp.headers.get("content-type", "application/json"))
            self.end_headers()
            self.wfile.write(resp_body)
            raise ProxyFatalError(reason)

        resp_body = resp.content
        if is_glm:
            resp_body = patch_json_signatures(resp_body)

        self.send_response(resp.status_code)
        self.send_header("Content-Type", resp.headers.get("content-type", "application/json"))
        self.end_headers()
        self.wfile.write(resp_body)

        # Track usage
        self._track_usage_from_body(resp_body, sid, level, provider_name)

    def _track_stream_usage(self, lines, sid, level, provider_name):
        """Parse usage from SSE stream.

        Anthropic splits usage across two events:
          - message_start: data.message.usage (input_tokens, cache_read/create)
          - message_delta:  data.usage         (output_tokens)
        GLM puts usage at top-level data.usage in both cases.
        """
        all_text = "".join(lines)
        # Collect all usage objects and merge
        merged_usage = {}
        for line in all_text.split("\n"):
            line = line.strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                data = json.loads(line[6:])
                # Top-level usage (message_delta, GLM)
                usage = data.get("usage")
                # Nested usage in message_start (Anthropic)
                msg_usage = data.get("message", {}).get("usage") if isinstance(data.get("message"), dict) else None
                for u in (usage, msg_usage):
                    if u and isinstance(u, dict):
                        for k, v in u.items():
                            if isinstance(v, (int, float)) and v > 0:
                                merged_usage[k] = max(merged_usage.get(k, 0), v)
            except Exception:
                continue
        if merged_usage:
            self._insert_usage(sid, level, provider_name, merged_usage)
        else:
            print(f"[msc] Warning: no usage found in stream for {provider_name}", flush=True)

    def _track_usage_from_body(self, resp_body, sid, level, provider_name):
        try:
            data = json.loads(resp_body)
            usage = data.get("usage")
            if usage:
                self._insert_usage(sid, level, provider_name, usage)
        except Exception:
            pass

    def _insert_usage(self, sid, level, provider_name, usage):
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        # Snapshot model name and label at time of usage
        level_info = self.route_levels.get(level, {})
        model_name = level_info.get("model", "")
        label = level_info.get("label", "")
        conn = db_connect()
        try:
            conn.execute(
                "INSERT INTO usage (session_id, level, provider, model_name, label, input_tokens, output_tokens, "
                "cache_read_tokens, cache_create_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, level, provider_name, model_name, label, input_tokens, output_tokens, cache_read, cache_create),
            )
            conn.commit()
        finally:
            conn.close()
        total = input_tokens + output_tokens
        print(f"[msc] Usage: {input_tokens}in + {output_tokens}out = {total} tokens ({provider_name})", flush=True)

    # ---- Helpers ----

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------
def _rebuild_clients():
    """Create one httpx.Client per provider based on current config."""
    old_clients = Handler.clients or {}
    for c in old_clients.values():
        try:
            c.close()
        except Exception:
            pass

    cfg = Handler.config
    clients = {}
    for pname, prov in cfg["providers"].items():
        if pname == "glm":
            clients[pname] = httpx.Client(timeout=600)
        else:
            # Anthropic or other providers that need proxy
            proxy = cfg.get("proxy")
            clients[pname] = httpx.Client(proxy=proxy, timeout=600) if proxy else httpx.Client(timeout=600)
    Handler.clients = clients


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
ROUTING_PROMPT_PATH = MSC_DIR / "routing-prompt.md"


def write_routing_prompt(cfg):
    """Write routing prompt file from config (called on start and config change)."""
    prompt = generate_routing_prompt(cfg)
    ROUTING_PROMPT_PATH.write_text(prompt + "\n")
    print(f"[msc] Routing prompt written to {ROUTING_PROMPT_PATH}", flush=True)


def main():
    cfg = load_config()
    Handler.config = cfg
    Handler.route_levels = build_route_levels(cfg)

    init_db()
    _rebuild_clients()
    write_routing_prompt(cfg)
    start_cot_indexer()

    # Start background health check thread (every 30 min)
    health_thread = threading.Thread(target=_health_check_loop, daemon=True)
    health_thread.start()

    # Start circuit breaker probe thread (every 5 min)
    cb_thread = threading.Thread(target=_cb_probe_loop, daemon=True)
    cb_thread.start()

    port = cfg.get("port", 3457)
    server = HTTPServer(("127.0.0.1", port), Handler)
    signal.signal(signal.SIGTERM, lambda *_: (server.shutdown(), sys.exit(0)))

    level_names = ", ".join(f"L{k}={v['label']}" for k, v in sorted(Handler.route_levels.items()))
    print(f"[msc] Listening on 127.0.0.1:{port}  Levels: {level_names}  Default: L{cfg['default_level']}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for c in (Handler.clients or {}).values():
            try:
                c.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
