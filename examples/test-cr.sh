#!/usr/bin/env bash
# test-cr.sh — 通过 cr 模式测试 MSC 完整链路
# 验证: session 注册 / 模型切换 / resume + 降级 / usage 记录
# 前提: source ~/.zshrc (cr 函数已加载)
# 用法: zsh examples/test-cr.sh
set -euo pipefail

source ~/.zshrc 2>/dev/null || true

export CLAUDE_CODE_DISABLE_1M_CONTEXT=1
PROXY="http://127.0.0.1:3457"

echo "╔═══════════════════════════════════════╗"
echo "║  MSC Test — cr mode                   ║"
echo "╚═══════════════════════════════════════╝"
echo ""

# 确保 proxy 在跑
if ! curl --noproxy '*' -s "$PROXY/" &>/dev/null; then
  echo "[test] Starting proxy..."
  PYTHONUNBUFFERED=1 nohup python3 ~/.claude/plugins/msc/proxy.py >> /tmp/msc-proxy.log 2>&1 &
  for i in $(seq 1 20); do curl --noproxy '*' -s "$PROXY/" &>/dev/null && break; sleep 0.3; done
fi
echo "[test] Proxy OK"

# 生成固定 session ID，方便 resume
SESSION_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
echo "[test] Session ID: ${SESSION_ID:0:8}..."

# ── Round 1: 复杂任务 → 应该触发模型升级 ──
echo ""
echo "── Round 1: 手撕红黑树（应触发升级到 L2+）──"
echo ""

cr --session-id "$SESSION_ID" \
  --allowedTools "Write,Bash" \
  -p "用 Python 实现一个红黑树，包含 insert、search、delete 操作，保存到 /tmp/msc-test-rbtree.py。写完整的单元测试覆盖各种旋转和颜色翻转场景，运行测试输出结果。" 2>&1 | tail -8

echo ""
echo "── Round 1 结果 ──"
[ -f /tmp/msc-test-rbtree.py ] && echo "[test] ✓ /tmp/msc-test-rbtree.py 已创建" || echo "[test] ✗ 未创建"

echo ""
echo "── Round 1 Session 记录 ──"
curl --noproxy '*' -s "$PROXY/sessions" | python3 -c "
import sys, json
for s in json.load(sys.stdin):
    for u in s.get('usage', []):
        print(f'  {s[\"session_id\"][:8]}… L{s[\"level\"]} ({s[\"label\"]}) | {u[\"provider\"]}: in={u[\"input_tokens\"]} out={u[\"output_tokens\"]}')
"

# ── Round 2: resume 同一 session，查金价（非编码，应降级到 L1）──
echo ""
echo "── Round 2: Resume + 查金价（应降级到 L1）──"
echo ""

cr --resume "$SESSION_ID" \
  --allowedTools "Bash,WebFetch" \
  -p "帮我查一下今天的国际金价是多少美元/盎司，一句话告诉我。" 2>&1 | tail -5

# ── 最终检查 ──
echo ""
echo "── 最终 Session 记录 ──"
curl --noproxy '*' -s "$PROXY/sessions" | python3 -c "
import sys, json
for s in json.load(sys.stdin):
    usage = s.get('usage', [])
    total = sum(u.get('input_tokens',0)+u.get('output_tokens',0) for u in usage)
    providers = ' + '.join(set(u.get('provider','?') for u in usage))
    print(f'  {s[\"session_id\"][:8]}… L{s[\"level\"]} ({s[\"label\"]}) | {total:,} tokens | {providers}')
"

echo ""
echo "[test] cr mode test complete"
rm -f /tmp/msc-test-rbtree.py
