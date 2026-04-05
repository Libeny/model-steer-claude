#!/usr/bin/env python3
"""
MSC + Claude Agent SDK Demo
============================

演示 Agent SDK 通过 MSC 代理网关实现模型自主路由。

流程：
  1. 给一个复杂需求（写红黑树），Claude 自主升级模型完成
  2. Resume 同一个 session，切回最便宜模型，闲聊验证降级

前提：
  1. MSC 已安装（bash install.sh）
  2. MSC proxy 已启动 (cr 或 python3 proxy.py)

运行：python3 examples/agent_with_msc.py
"""

import asyncio
import os
from pathlib import Path
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

MSC_PLUGIN = str(Path.home() / ".claude" / "plugins" / "msc")
ROUTING_PROMPT = Path.home() / ".msc" / "routing-prompt.md"

os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:3457"
os.environ["NO_PROXY"] = "127.0.0.1"
os.environ["CLAUDE_CODE_DISABLE_1M_CONTEXT"] = "1"


async def main():
    print("MSC + Claude Agent SDK Demo")
    print("=" * 50)

    routing_prompt = ROUTING_PROMPT.read_text() if ROUTING_PROMPT.exists() else ""

    sdk_opts = dict(
        plugins=[{"type": "local", "path": MSC_PLUGIN}],
        env={"MSC_ENABLED": "1"},
        system_prompt=routing_prompt,
    )

    # ── Round 1: 复杂任务 → Claude 自主决策升级模型 ──
    print("\n── Round 1: 写红黑树（Claude 自主选择模型）──\n")

    session_id = None

    async for msg in query(
        prompt="用 Python 实现一个红黑树，包含 insert 和 search，保存到 /tmp/rbtree.py 并测试。",
        options=ClaudeAgentOptions(
            max_turns=15,
            allowed_tools=["Write", "Bash"],
            permission_mode="acceptEdits",
            **sdk_opts,
        ),
    ):
        if hasattr(msg, "content"):
            print(msg.content)
        if isinstance(msg, ResultMessage):
            session_id = msg.session_id

    if not session_id:
        print("[error] 未获取到 session_id")
        return

    print(f"\n[msc] Session ID: {session_id[:8]}...")

    # ── Round 2: Resume 同一个 session，闲聊验证降级 ──
    print("\n── Round 2: Resume + 闲聊（验证模型降级）──\n")

    async for msg in query(
        prompt="你觉得红黑树和 AVL 树哪个更实用？一句话回答。",
        options=ClaudeAgentOptions(
            resume=session_id,
            max_turns=3,
            **sdk_opts,
        ),
    ):
        if hasattr(msg, "content"):
            print(msg.content)

    print("\n── Done ──")


if __name__ == "__main__":
    asyncio.run(main())
