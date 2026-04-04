#!/usr/bin/env python3
"""msc proxy: config-driven multi-level Claude routing with SQLite persistence."""
import json
import os
import signal
import sqlite3
import sys
import tempfile
import time
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
    """Build {int_level: {name, provider, model, label}} from config."""
    levels = {}
    for k, v in cfg["levels"].items():
        lvl = int(k)
        levels[lvl] = {
            "name": v["name"],
            "provider": v["provider"],
            "model": v["model"],
            "label": v["name"].upper(),
        }
    return levels


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
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_create_tokens INTEGER DEFAULT 0,
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    conn.commit()
    conn.close()


def db_connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


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
# Handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    config = None
    route_levels = None
    clients = None  # {provider_name: httpx.Client}
    active_session = None

    def log_message(self, fmt, *args):
        pass

    # ---- GET endpoints ----

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        sid = params.get("session", [""])[0]

        if path == "/":
            self._json({"status": "ok"})

        elif path == "/register" and sid:
            self._handle_register(sid)

        elif path == "/think-level" and sid:
            level_str = params.get("level", [""])[0]
            self._handle_think_level(sid, level_str)

        elif path == "/status" and sid:
            self._handle_status(sid)

        elif path == "/config":
            self._handle_config_get()

        elif path == "/sessions":
            self._handle_sessions()

        elif path == "/ui":
            self._handle_ui()

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
                    "SELECT provider, SUM(input_tokens) as inp, SUM(output_tokens) as out FROM usage WHERE session_id = ? GROUP BY provider",
                    (sid,),
                ).fetchall()
                usage = [{"provider": u[0], "input_tokens": u[1], "output_tokens": u[2]} for u in usage_rows]
                level_info = Handler.route_levels.get(level, {})
                result.append({
                    "session_id": sid, "level": level,
                    "name": level_info.get("name", "?"), "label": level_info.get("label", "?"),
                    "created_at": created, "updated_at": updated, "usage": usage,
                })
            self._json(result)
        finally:
            conn.close()

    def _handle_config_get(self):
        cfg = load_config()
        # Mask provider keys
        masked = json.loads(json.dumps(cfg))
        for pname, prov in masked.get("providers", {}).items():
            if "key" in prov:
                prov["key"] = mask_key(prov["key"])
        self._json(masked)

    def _handle_ui(self):
        try:
            content = DASHBOARD_PATH.read_text()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode())
        except FileNotFoundError:
            self._json({"error": "dashboard.html not found"}, status=404)

    # ---- POST endpoints ----

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        if path == "/config":
            self._handle_config_post(raw)
            return

        if not path.startswith("/v1/messages"):
            self._json({"error": "not found"}, status=404)
            return

        self._handle_proxy(raw)

    def _handle_config_post(self, raw):
        try:
            new_cfg = json.loads(raw)
            save_config(new_cfg)
            # Reload
            Handler.config = load_config()
            Handler.route_levels = build_route_levels(Handler.config)
            _rebuild_clients()
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
        fallback_order = get_fallback_order(level, max_level)
        retry_cfg = self.config.get("retry", {"max_attempts": 3, "interval_seconds": 2})

        last_error = None
        for try_level in fallback_order:
            level_info = self.route_levels[try_level]
            provider_name = level_info["provider"]
            provider_cfg = self.config["providers"][provider_name]
            client = self.clients[provider_name]

            for attempt in range(retry_cfg["max_attempts"]):
                try:
                    self._do_proxy_request(body, try_level, level_info, provider_name, provider_cfg, client, sid)
                    return
                except Exception as e:
                    last_error = e
                    print(f"[msc] Error L{try_level} attempt {attempt + 1}: {e}", flush=True)
                    if attempt < retry_cfg["max_attempts"] - 1:
                        time.sleep(retry_cfg["interval_seconds"])

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
            headers["x-api-key"] = provider_cfg["key"]
            req_body["model"] = level_info["model"]
        else:
            # Anthropic: passthrough all headers (OAuth compatible), fix signatures
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length")}
            n = fix_signatures(req_body.get("messages", []))
            if n:
                print(f"[msc] Fixed {n} signature(s)", flush=True)
            if not is_glm:
                url = provider_cfg["url"] + self.path

        content = json.dumps(req_body)
        print(f"[msc] → Level {level} ({level_info['label']})", flush=True)

        if is_stream:
            self._stream_response(client, url, headers, content, is_glm, sid, level, provider_name)
        else:
            self._non_stream_response(client, url, headers, content, is_glm, sid, level, provider_name)

    def _stream_response(self, client, url, headers, content, is_glm, sid, level, provider_name):
        with client.stream("POST", url, headers=headers, content=content) as resp:
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
        """Parse final message_delta event for usage in SSE stream."""
        all_text = "".join(lines)
        for line in reversed(all_text.split("\n")):
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            try:
                data = json.loads(line[6:])
                usage = data.get("usage")
                if usage:
                    self._insert_usage(sid, level, provider_name, usage)
                    return
            except Exception:
                continue

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
        conn = db_connect()
        try:
            conn.execute(
                "INSERT INTO usage (session_id, level, provider, input_tokens, output_tokens, "
                "cache_read_tokens, cache_create_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, level, provider_name, input_tokens, output_tokens, cache_read, cache_create),
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
def main():
    cfg = load_config()
    Handler.config = cfg
    Handler.route_levels = build_route_levels(cfg)

    init_db()
    _rebuild_clients()

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
