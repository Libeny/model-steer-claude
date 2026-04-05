#!/usr/bin/env python3
"""
test-sdk.py — 通过 Agent SDK (plugins=[]) 测试 MSC 完整链路
验证: session 注册 / 模型切换 / resume / usage 记录
用法: python3 examples/test-sdk.py
"""

import asyncio
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

MSC_PLUGIN = str(Path.home() / ".claude" / "plugins" / "msc")
PROXY = "http://127.0.0.1:3457"
ROUTING_PROMPT = Path.home() / ".msc" / "routing-prompt.md"

os.environ["ANTHROPIC_BASE_URL"] = PROXY
os.environ["NO_PROXY"] = "127.0.0.1"
os.environ["CLAUDE_CODE_DISABLE_1M_CONTEXT"] = "1"


def ensure_proxy():
    try:
        urllib.request.urlopen(PROXY, timeout=1)
        return True
    except Exception:
        pass
    print("[test] Starting proxy...")
    subprocess.Popen(
        ["python3", f"{MSC_PLUGIN}/proxy.py"],
        stdout=open("/tmp/msc-proxy.log", "a"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    for _ in range(20):
        try:
            urllib.request.urlopen(PROXY, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def print_sessions(label=""):
    try:
        resp = urllib.request.urlopen(f"{PROXY}/sessions", timeout=2)
        sessions = json.loads(resp.read())
        print(f"{label}Total sessions: {len(sessions)}")
        for s in sessions:
            usage = s.get("usage", [])
            for u in usage:
                inp, out = u.get("input_tokens", 0), u.get("output_tokens", 0)
                cr, cc = u.get("cache_read_tokens", 0), u.get("cache_create_tokens", 0)
                print(f"  {s['session_id'][:8]}… L{s['level']} ({s['label']}) | {u['provider']}: in={inp} out={out} cache_r={cr} cache_c={cc}")
    except Exception as e:
        print(f"  Error: {e}")


async def main():
    print("╔═══════════════════════════════════════╗")
    print("║  MSC Test — Agent SDK (plugins=[])    ║")
    print("╚═══════════════════════════════════════╝")
    print()

    if not ensure_proxy():
        print("[test] ✗ Proxy failed to start")
        return
    print("[test] Proxy OK")

    # Read routing prompt (dynamically generated from config by proxy)
    routing_prompt = ROUTING_PROMPT.read_text() if ROUTING_PROMPT.exists() else ""

    sdk_opts = dict(
        plugins=[{"type": "local", "path": MSC_PLUGIN}],
        env={"MSC_ENABLED": "1"},
        system_prompt=routing_prompt,
    )

    # ── Round 1: 复杂任务 → 应该触发模型升级 ──
    print()
    print("── Round 1: 手撕红黑树（应触发升级到 L2+）──")
    print()

    session_id = None
    async for msg in query(
        prompt="用 Python 实现一个红黑树，包含 insert、search、delete 操作，保存到 /tmp/msc-test-rbtree.py。写完整的单元测试覆盖各种旋转和颜色翻转场景，运行测试输出结果。",
        options=ClaudeAgentOptions(
            max_turns=10,
            allowed_tools=["Write", "Bash"],
            permission_mode="acceptEdits",
            **sdk_opts,
        ),
    ):
        if hasattr(msg, "content"):
            # Only print text blocks
            if isinstance(msg.content, str):
                print(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if hasattr(block, "text"):
                        print(block.text)
        if isinstance(msg, ResultMessage):
            session_id = msg.session_id

    print()
    print("── Round 1 结果 ──")
    test_file = Path("/tmp/msc-test-rbtree.py")
    if test_file.exists():
        print(f"[test] ✓ {test_file} 已创建")
    else:
        print(f"[test] ✗ {test_file} 未创建")

    if session_id:
        print(f"[test] Session ID: {session_id[:8]}...")

    print()
    print("── Session & 模型切换记录 ──")
    print_sessions()

    # ── Round 2: resume 同一 session，查金价（非编码，应降级到 L1）──
    if session_id:
        print()
        print("── Round 2: Resume + 查金价（应降级到 L1）──")
        print()

        async for msg in query(
            prompt="帮我用 WebFetch 查一下今天的国际金价是多少美元/盎司，一句话告诉我。",
            options=ClaudeAgentOptions(
                resume=session_id,
                max_turns=5,
                allowed_tools=["Bash", "WebFetch"],
                **sdk_opts,
            ),
        ):
            if hasattr(msg, "content"):
                if isinstance(msg.content, str):
                    print(msg.content)
                elif isinstance(msg.content, list):
                    for block in msg.content:
                        if hasattr(block, "text"):
                            print(block.text)

    # ── 最终检查 ──
    print()
    print("── 最终 Session 记录 ──")
    print_sessions()

    print()
    print("[test] Agent SDK test complete")

    # Cleanup
    test_file.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
