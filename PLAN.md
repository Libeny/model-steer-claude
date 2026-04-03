# claude-code-claw — 智能模型路由代理

## Context

用户已有一个可工作的原型（`~/cr-proxy.py`），实现了 Claude Code 请求在 GLM（便宜）和 Anthropic Sonnet/Opus（强大）之间的路由切换。现在要把它做成开源项目 `~/work/claude-code-claw/`，加入自动决策、降级链、持久化、skill、可视化配置等完整功能。

## Codex Review 修复记录

| Round | Issue | 修复 |
|-------|-------|------|
| R1 ❌ | provider/model/level 三层语义混淆 | `RouteLevel` 统一抽象 |
| R1 ❌ | 接口命名冲突 | 冻结：`/smoke` `/redbull` `/think-level` |
| R1 ❌ | `/tmp/cr-session` 并发风险 | SQLite 数据库替代文件 |
| R1 ⚠️ | 无原子写入 | SQLite 事务自带 |
| R1 ⚠️ | install.sh 改动范围过大 | 分步安装 `--core --hooks --skills --shell` |
| R2 ❌ | fallback 方向二义性 | 失败先降级再升级 |
| R2 ❌ | session 来源 `latest` 回退 | `CLAUDE_ENV_FILE` 注入 `$CR_SESSION` |
| R3 ❌ | hook 时序 session 来源冲突 | hook 通过 `CLAUDE_ENV_FILE` 直接 export |
| R4 ❌ | `$CR_SESSION` 与真实 session_id 无桥接 | `CLAUDE_ENV_FILE` 彻底消除问题 |

## 核心抽象：RouteLevel

```python
# 统一路由层级，消除 provider/model/level 混淆
ROUTE_LEVELS = {
    1: {"name": "glm",    "provider": "glm",       "model": "glm-5.1",               "label": "GLM"},
    2: {"name": "sonnet", "provider": "anthropic",  "model": "claude-sonnet-4-6",     "label": "Sonnet"},
    3: {"name": "opus",   "provider": "anthropic",  "model": "claude-opus-4-6",       "label": "Opus"},
}
```

所有接口统一引用 level 或 name：
- `/use-glm?session=X` = 设置 level 1
- `/use-sonnet?session=X` = 设置 level 2
- `/use-opus?session=X` = 设置 level 3
- `/think-level?session=X&level=2` = 等效于 `/use-sonnet`
- `/status?session=X` → `{"level": 2, "name": "sonnet", "label": "Sonnet"}`

**Fallback 方向（成本优先）：** 失败时先降级（找更便宜的），降级走完再升级（最后手段）。
顺序：当前 level → 向下逐级 → 向上逐级 → 502。用户主动切换不触发 fallback。

## 项目结构

```
claude-code-claw/
├── proxy.py                     # 核心代理（HTTP server + SQLite）
├── install.sh                   # 分步安装（--core --hooks --skills --shell）
├── hooks/
│   ├── session-start.sh         # CLAUDE_ENV_FILE 注入 CR_SESSION + 注册 proxy
│   └── user-prompt-submit.sh    # 注入当前 level 到模型上下文（用 $CR_SESSION）
├── skills/
│   ├── smoke/SKILL.md           # /smoke → 降到 level 1 (GLM)
│   ├── redbull/SKILL.md         # /redbull → 升到 level 3 (Opus)
│   └── think-level/SKILL.md     # /think-level 1|2|3
├── ui/
│   └── dashboard.html           # 内嵌单文件 Web UI（零依赖）
├── CLAUDE.md                    # 项目级自动路由指令
├── tools/
│   ├── usage-stats.py           # 用量统计（模糊搜索 session_id）
│   └── fix-thinking-blocks.py   # thinking 签名修复
├── config/
│   └── default-config.json      # 默认配置模板
├── README.md
└── .gitignore
```

## 关键架构决策

### 1. Session ID 传递：CLAUDE_ENV_FILE

SessionStart hook 通过 `CLAUDE_ENV_FILE` 机制将真实 session_id 注入为环境变量 `$CR_SESSION`。模型 Bash 工具直接读取，无需文件回退、无需 ITERM_SESSION_ID、无需双向映射。

```
claude 启动 → 生成 session_id → SessionStart hook
  → hook 从 stdin 读 session_id
  → echo "export CR_SESSION=$SESSION_ID" >> $CLAUDE_ENV_FILE
  → curl /register?session=$SESSION_ID 注册到 proxy
  → 模型的所有 Bash 命令自动可用 $CR_SESSION
```

### 2. 存储：SQLite（~/.claude-code-claw/claw.db）

替代 JSON 文件，解决原子写入、并发安全、模糊查询：

```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    level INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE usage (
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
);
```

### 3. Fallback 方向：先降级再升级

```
请求 Level 2 (Sonnet) 失败
  → 重试 3 次（间隔 2s）
  → 降级 Level 1 (GLM) → 重试 3 次
  → 升级 Level 3 (Opus) → 重试 3 次（最后手段）
  → 全部失败 → 502

即：当前 level → 向下走 → 走完后向上走 → 全部失败 502
```

成本优先：失败时先找更便宜的，实在不行才用更贵的。

## 实现计划

### Phase 1：Core

**Task 1.1: `config/default-config.json`**

```json
{
  "port": 3457,
  "proxy": "http://127.0.0.1:7897",
  "default_level": 1,
  "levels": {
    "1": {"name": "glm",    "provider": "glm",      "model": "glm-5.1"},
    "2": {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-4-6"},
    "3": {"name": "opus",   "provider": "anthropic", "model": "claude-opus-4-6"}
  },
  "providers": {
    "glm": {"url": "https://open.bigmodel.cn/api/anthropic/v1/messages", "key": "YOUR_KEY"},
    "anthropic": {"url": "https://api.anthropic.com", "passthrough_auth": true}
  },
  "retry": {"max_attempts": 3, "interval_seconds": 2}
}
```

验收：`python3 -c "import json; json.load(open('config/default-config.json'))"` 无报错

**Task 1.2: `proxy.py`** — ~450 行，从 `~/cr-proxy.py` 演进

核心改动：
- 启动时加载 `~/.claude-code-claw/config.json`，缺失用 default-config.json
- `ROUTE_LEVELS` 从配置构建，每个 provider 独立 `httpx.Client`
- SQLite 存储：`~/.claude-code-claw/claw.db`（sessions 表 + usage 表）
- Fallback：同 provider 重试 3 次 → 先降级（level-1）→ 再升级（level+1）→ 502
- GLM 路由：替换 auth + model 字段
- Anthropic 路由：透传全部 header（OAuth 兼容）
- 签名修复：保留 StreamSignaturePatcher + fix_signatures + patch_json_signatures
- 内置 token 追踪：解析响应 usage，INSERT INTO usage 表

端点清单：
| 端点 | 方法 | 作用 |
|------|------|------|
| `/` | GET | 健康检查 |
| `/use-glm?session=X` | GET | 切 level 1 |
| `/use-sonnet?session=X` | GET | 切 level 2 |
| `/use-opus?session=X` | GET | 切 level 3 |
| `/think-level?session=X&level=N` | GET | 切指定 level |
| `/register?session=X` | GET | 注册 session（hook 调用） |
| `/status?session=X` | GET | 当前 session 路由状态 |
| `/stats?session=X` | GET | session token 用量 |
| `/config` | GET | 返回当前配置（脱敏） |
| `/config` | POST | 更新配置 |
| `/sessions` | GET | 所有活跃 session 列表 |
| `/ui` | GET | Dashboard HTML |
| `/v1/messages*` | POST | 主路由（代理请求） |

验收：
```bash
python3 proxy.py &
curl --noproxy '*' -s http://127.0.0.1:3457/ | jq .status  # "ok"
curl --noproxy '*' -s http://127.0.0.1:3457/register?session=test001
curl --noproxy '*' -s http://127.0.0.1:3457/status?session=test001 | jq .level  # 1
curl --noproxy '*' -s http://127.0.0.1:3457/use-sonnet?session=test001
curl --noproxy '*' -s http://127.0.0.1:3457/status?session=test001 | jq .level  # 2
sqlite3 ~/.claude-code-claw/claw.db "SELECT level FROM sessions WHERE session_id='test001'"  # 2
# 重启 proxy 后：
curl --noproxy '*' -s http://127.0.0.1:3457/status?session=test001 | jq .level  # 仍然是 2（从 SQLite 恢复）
```

**Task 1.3: `hooks/session-start.sh`** — ~30 行 bash

核心机制：`CLAUDE_ENV_FILE` 注入 `$CR_SESSION`

```bash
#!/bin/bash
# 从 stdin 读 session_id
SESSION_ID=$(cat | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

# 1. 注入环境变量（模型 Bash 工具自动可用）
if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo "export CR_SESSION=$SESSION_ID" >> "$CLAUDE_ENV_FILE"
fi

# 2. 注册到 proxy
curl --noproxy '*' -s -m 0.5 "http://127.0.0.1:3457/register?session=$SESSION_ID" >/dev/null 2>&1

# 3. stdout 注入模型上下文
STATUS=$(curl --noproxy '*' -s -m 0.5 "http://127.0.0.1:3457/status?session=$SESSION_ID" 2>/dev/null)
LEVEL=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('level',1))" 2>/dev/null || echo 1)
echo "[claw] Session: ${SESSION_ID:0:8} | Level $LEVEL | /think-level 1|2|3 | /smoke | /redbull"
```

时序预算：stdin 立即读完（pipe）+ 2x curl 0.5s 超时 = ~1s

验收：
```bash
# proxy 运行中
echo '{"session_id":"test-001"}' | CLAUDE_ENV_FILE=/tmp/test-env bash hooks/session-start.sh
grep CR_SESSION /tmp/test-env  # export CR_SESSION=test-001
curl --noproxy '*' -s http://127.0.0.1:3457/status?session=test-001  # 已注册
```

**Task 1.4: `CLAUDE.md`** — 项目级自动路由指令

决策规则（CLAUDE.md 管"何时切"）：
- 简单 Q&A / 闲聊 → 不切（留在当前 level）
- 代码生成 / debugging → 升到 level 2+ 
- 架构设计 / 复杂推理 → 升到 level 3
- 任务完成 / 对话收尾 → 降回 level 1（自动 /smoke）

读 session_id：**只用 `$CR_SESSION`**（由 `cr()` 函数注入）。无 fallback，无 `latest` 文件。如果 `$CR_SESSION` 为空则不执行切换。

验收：文件存在且包含 curl 命令模板

**Task 1.5: `.gitignore`**

```
__pycache__/
*.pyc
.DS_Store
*.log
node_modules/
```

### Phase 2：Integration

**Task 2.1: `install.sh`** — 分步安装，每步可选

```bash
./install.sh                    # 全部安装（默认）
./install.sh --core             # 只安装 proxy + 配置
./install.sh --hooks            # 只安装 hooks 到 settings.json
./install.sh --skills           # 只安装 skills
./install.sh --shell            # 只添加 cr() 到 .zshrc
./install.sh --uninstall        # 反向清理全部
```

合并策略：
- settings.json hooks 是数组，append 不覆盖
- 用 `python3 -c` 做 JSON 合并
- 幂等：检查 command 字符串已存在则跳过
- `--uninstall` 反向清理

验收：
```bash
./install.sh --core && ls ~/.claude-code-claw/config.json  # core only
./install.sh --hooks && python3 -c "import json; h=json.load(open('$HOME/.claude/settings.json')); print([x for x in h['hooks']['SessionStart'] if 'claw' in str(x)])"  # hook 存在
./install.sh           # 二次全量运行，无重复（幂等）
./install.sh --uninstall && ! ls ~/.claude-code-claw/ 2>/dev/null  # 清理干净
```

**Task 2.2: `hooks/user-prompt-submit.js`** — ~40 行

- 查询 `/status?session=X` 获取当前 level
- stdout 输出 `[claw: Level 2 (Sonnet)]`
- proxy 不可达时静默失败

**Task 2.3: Skills**

| Skill | 文件 | 触发 | 逻辑 |
|-------|------|------|------|
| `/smoke` | `skills/smoke/SKILL.md` | "休息"/"降级"/"省钱" | 读 session → curl /use-glm → 确认 "Level 1 (GLM)" |
| `/redbull` | `skills/redbull/SKILL.md` | "全力"/"升级"/"深度思考" | 读 session → curl /use-opus → 确认 "Level 3 (Opus)" |
| `/think-level` | `skills/think-level/SKILL.md` | "/think-level 1\|2\|3" | 读 session → curl /think-level?level=N → 确认 |

每个 skill 内部：
1. 读 session_id：**只用 `$CR_SESSION`**（无 fallback）
2. 查 `/status` 获取当前 level
3. 执行 curl 切换
4. 确认输出：`[claw] Switched to Level N (Name)`

验收：在 `cr` 交互模式中执行 `/smoke` `/redbull` `/think-level 2`，proxy 日志显示切换

### Phase 3：Polish

**Task 3.1: `ui/dashboard.html`** — 内嵌单文件 Web UI

proxy 的 `/ui` 返回此文件。零依赖（内嵌 CSS + JS），功能：
- Provider 状态（健康/名称/当前费用）
- 活跃 Session 列表（level、耗时、token 数）
- Fallback Chain 可视化
- 当日用量汇总（各模型 token 数 + 费用 + 节省比例）
- 配置编辑（修改 provider key、调整 level 映射）

**MVP 范围（先收敛）：**
- Session 列表 + 当前 level + 基础 token stats
- 配置只读查看（编辑功能后续迭代）
- 不含费用计算/provider 健康检测（后续迭代）

数据来源：轮询 `/sessions`、`/stats`、`/config` 接口

验收：
```bash
curl --noproxy '*' -s http://127.0.0.1:3457/ui | grep -q '<html'  # HTML 返回
curl --noproxy '*' -s http://127.0.0.1:3457/sessions | jq .       # JSON 数组
```

**Task 3.2: `tools/fix-thinking-blocks.py`** — 从 `~/fix-session-thinking-blocks.py` 复制

验收：`python3 tools/fix-thinking-blocks.py --help` 正常输出

**Task 3.3: `tools/usage-stats.py`** — 从 `~/claude-session-usage.py` 改造

新增：
- 模糊搜索：`usage-stats.py e664` 匹配 `e664a78a-...`（在 `~/.claude/projects/` 目录下搜索）
- 实时查询：`--live` 轮询 proxy `/stats` 端点
- 整合 proxy 内置统计

验收：
```bash
python3 tools/usage-stats.py e664    # 模糊搜索
python3 tools/usage-stats.py --all   # 全量统计
```

**Task 3.4: `README.md`**

包含：架构图、快速开始、配置参考、Skill 列表、FAQ

## 关键技术细节

### Fallback 逻辑（先降级再升级，成本优先）
```
请求 level 2 (Sonnet) → 失败
  → 同 provider 重试 3 次（间隔 2s）
  → 降级 level 1 (GLM) → 重试 3 次    ← 先找更便宜的
  → 升级 level 3 (Opus) → 重试 3 次    ← 最后手段
  → 全部失败 → 返回 502

顺序：当前 level → 向下逐级 → 向上逐级 → 502
```

用户主动 `/smoke` `/redbull` `/think-level` 不触发 fallback，直接切。

### Auth 路由
- provider=glm：去掉原始 auth header，注入 provider.key
- provider=anthropic：透传全部 header（OAuth Bearer token 直通）

### 签名修复（双向）
- 请求方向：`fix_signatures(messages)` 修复历史中 GLM 的空签名
- 响应方向：`StreamSignaturePatcher` 修复 GLM 新返回的空签名（跨 chunk buffer 安全）

### 存储：SQLite

```
~/.claude-code-claw/claw.db
  sessions: session_id, level, created_at, updated_at
  usage:    session_id, level, provider, input_tokens, output_tokens, cache_*, timestamp
```

SQLite 自带事务原子性、并发安全（WAL 模式）、模糊查询。proxy 为唯一写入者。

### Hook 时序（CLAUDE_ENV_FILE 机制）
```
claude 启动 → 生成 session_id → SessionStart hook 触发
  → hook 从 stdin 读 {"session_id":"xxx","source":"startup"}
  → echo "export CR_SESSION=xxx" >> $CLAUDE_ENV_FILE    ← 注入环境变量
  → curl /register?session=xxx                           ← 注册到 proxy（写 SQLite）
  → stdout: "[claw] Session: xxx | Level 1 (GLM)"       ← 注入模型上下文
  → 模型加载 CLAUDE.md → 用户首条消息
  → 模型 Bash 工具自动有 $CR_SESSION → curl 切换
```

**Session 来源唯一性：** `$CR_SESSION` 通过 `CLAUDE_ENV_FILE` 在 SessionStart 时注入，是模型获取 session_id 的唯一来源。无文件回退、无 ITERM_SESSION_ID、无 `latest` 概念。SQLite 仅供 proxy 内部使用。

**user-prompt-submit hook** 同样使用 `$CR_SESSION`（已在环境中）：
```bash
STATUS=$(curl --noproxy '*' -s -m 0.5 "http://127.0.0.1:3457/status?session=$CR_SESSION")
echo "[claw: Level $(echo $STATUS | python3 -c 'import sys,json;print(json.load(sys.stdin).get(\"level\",\"?\"))')]"
```

### install.sh 合并策略
- settings.json hooks 是数组，append 新条目
- `python3 -c` 做 JSON 合并（幂等：检查 command 字符串已存在则跳过）
- `--uninstall` 反向移除

### cr() 函数
```bash
cr() {
  # 启动 proxy（如未运行）
  curl --noproxy '*' -s http://127.0.0.1:3457/ &>/dev/null || {
    PYTHONUNBUFFERED=1 nohup python3 ~/work/claude-code-claw/proxy.py >> /tmp/cr-proxy.log 2>&1 &
    for i in {1..20}; do curl --noproxy '*' -s http://127.0.0.1:3457/ &>/dev/null && break; sleep 0.2; done
  }
  # CR_SESSION 不需要在这里设——SessionStart hook 通过 CLAUDE_ENV_FILE 自动注入
  CLAUDE_CODE_DISABLE_1M_CONTEXT=1 NO_PROXY=127.0.0.1 ANTHROPIC_BASE_URL=http://127.0.0.1:3457 claude "$@"
}
```

不再需要 `export CR_SESSION`、`ITERM_SESSION_ID`、`--resume` 解析。hook 自动处理一切。

## 现有代码引用

| 文件 | 作用 | 复用方式 |
|------|------|---------|
| `~/cr-proxy.py` | 工作原型 | 演进为 proxy.py |
| `~/claude-session-usage.py` | 用量统计 | 改造为 tools/usage-stats.py |
| `~/fix-session-thinking-blocks.py` | 签名修复 | 直接复制 |
| `~/work/clawd-on-desk/hooks/clawd-hook.js` | Hook 模式 | 参考 stdin 解析 |
| `~/work/MuYu/.claude/skills/codex-plan-review/` | Skill 格式 | 参考 SKILL.md 结构 |
