# claude-code-claw — 智能模型路由代理

## Context

用户已有一个可工作的原型（`~/cr-proxy.py`），实现了 Claude Code 请求在 GLM（便宜）和 Anthropic Sonnet/Opus（强大）之间的路由切换。现在要把它做成开源项目 `~/work/claude-code-claw/`，加入自动决策、降级链、持久化、skill、可视化配置等完整功能。

## Codex Review Round 1 修复

| Issue | 修复 |
|-------|------|
| ❌ provider/model/level 三层语义混淆 | 定义 `RouteLevel` 统一抽象：level → provider → model |
| ❌ 接口命名冲突（think-harder vs smoke/redbull） | 冻结最终命名：`/smoke` `/redbull` `/think-level` |
| ❌ `/tmp/cr-session` 并发风险 | 改为 `~/.claude-code-claw/sessions/<session_id>.json` |
| ⚠️ routes.json 无原子写入 | 临时文件 + rename 原子写 |
| ⚠️ install.sh 改动范围过大 | 分步安装：core → hooks → skills（可选） |
| ⚠️ 验收标准不够任务化 | 每个 task 附带可执行验收命令 |

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

降级链固定：3 → 2 → 1（Opus → Sonnet → GLM），配置文件里可以扩展更多 level。

## 项目结构

```
claude-code-claw/
├── proxy.py                     # 核心代理
├── install.sh                   # 分步安装
├── hooks/
│   ├── session-start.js         # 捕获 session_id，注册到 proxy
│   └── user-prompt-submit.js    # 注入当前模型/level 信息
├── skills/
│   ├── smoke/SKILL.md           # /smoke → level 1 (GLM)
│   ├── redbull/SKILL.md         # /redbull → level 3 (Opus)
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

**Task 1.2: `proxy.py`** — ~400 行，从 `~/cr-proxy.py` 演进

核心改动：
- 启动时加载 `~/.claude-code-claw/config.json`，缺失用 default-config.json
- `ROUTE_LEVELS` 从配置构建，每个 provider 独立 `httpx.Client`
- Session 状态持久化到 `~/.claude-code-claw/sessions/<session_id>.json`（原子写：写临时文件 + rename）
- 降级重试：同 provider 重试 3 次 → fallback 到 level+1 或 level-1（按降级链）
- GLM 路由：替换 auth + model 字段
- Anthropic 路由：透传全部 header（OAuth 兼容）
- 签名修复：保留 StreamSignaturePatcher + fix_signatures + patch_json_signatures
- 内置 token 追踪：解析响应 usage，按 session+level 累计

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
cat ~/.claude-code-claw/sessions/test001.json  # 持久化验证
# 重启 proxy 后：
curl --noproxy '*' -s http://127.0.0.1:3457/status?session=test001 | jq .level  # 仍然是 2
```

**Task 1.3: `hooks/session-start.js`** — ~50 行

参考：`~/work/clawd-on-desk/hooks/clawd-hook.js` 的 stdin 解析模式

流程：
1. 读 stdin JSON → 提取 `session_id`
2. `curl --noproxy '*' http://127.0.0.1:3457/register?session=<id>`
3. 写 `~/.claude-code-claw/sessions/<session_id>.json`（备份，供模型读取）
4. stdout 输出 session 状态（注入模型上下文）：
```
[claw] Session: <session_id_short>
Current: Level 1 (GLM) | /think-level 1|2|3 | /smoke | /redbull
```

时序预算：400ms stdin + 500ms HTTP = 900ms < 1s

验收：
```bash
echo '{"session_id":"test-hook-001","source":"startup"}' | node hooks/session-start.js
# stdout 应包含 [claw] Session: test-hoo
# proxy 日志应显示 register
```

**Task 1.4: `CLAUDE.md`** — 项目级自动路由指令

决策规则（CLAUDE.md 管"何时切"）：
- 简单 Q&A / 闲聊 → 不切（留在当前 level）
- 代码生成 / debugging → 升到 level 2+ 
- 架构设计 / 复杂推理 → 升到 level 3
- 任务完成 / 对话收尾 → 降回 level 1（自动 /smoke）

读 session_id：先 `$CR_SESSION`，fallback `cat ~/.claude-code-claw/sessions/latest`

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

```
Step 1 (core):    创建 ~/.claude-code-claw/ + 默认配置
Step 2 (hooks):   合并 SessionStart + UserPromptSubmit hook 到 settings.json
Step 3 (skills):  安装 /smoke /redbull /think-level 到 ~/.claude/skills/
Step 4 (shell):   添加 cr() 函数到 .zshrc
```

合并策略：
- settings.json hooks 是数组，append 不覆盖
- 用 `python3 -c` 做 JSON 合并
- 幂等：检查已存在则跳过
- `--uninstall` 反向清理

验收：
```bash
./install.sh           # 首次安装
./install.sh           # 二次运行，无重复
./install.sh --uninstall  # 清理
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
1. 读 session_id：`$CR_SESSION` 或最近的 session 文件
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

数据来源：轮询 `/sessions`、`/stats`、`/config` 接口

验收：`open http://127.0.0.1:3457/ui`，页面正常渲染，数据刷新

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

### 降级重试逻辑
```
请求 level 1 (GLM) → 失败
  → 同 provider 重试 3 次（间隔 2s）
  → 仍失败 → 降级到 level 2 (Sonnet) → 重试 3 次
  → 仍失败 → 降级到 level 3 (Opus) → 重试 3 次
  → 全部失败 → 返回 502 + 错误详情
```

注意：降级方向是"质量升级"（GLM 挂了用 Sonnet），不是"质量降级"。

### Auth 路由
- provider=glm：去掉原始 auth header，注入 provider.key
- provider=anthropic：透传全部 header（OAuth Bearer token 直通）

### 签名修复（双向）
- 请求方向：`fix_signatures(messages)` 修复历史中 GLM 的空签名
- 响应方向：`StreamSignaturePatcher` 修复 GLM 新返回的空签名（跨 chunk buffer 安全）

### Session 持久化
```
~/.claude-code-claw/sessions/<session_id>.json
{
  "session_id": "e664a78a-...",
  "level": 2,
  "name": "sonnet",
  "created_at": "...",
  "updated_at": "...",
  "usage": {"glm": {...}, "sonnet": {...}}
}
```
写入方式：写临时文件 `<session_id>.json.tmp` → `os.rename()` 原子替换

### Hook 时序
```
claude 启动 → 生成 session_id → SessionStart hook 触发
  → hook 读 stdin {"session_id":"xxx","source":"startup"}
  → curl /register?session=xxx
  → 写 ~/.claude-code-claw/sessions/xxx.json
  → stdout: "[claw] Session: xxx | Level 1 (GLM) | /think-level 1|2|3 | /smoke | /redbull"
  → 模型加载 CLAUDE.md → 用户首条消息
  → 模型读 $CR_SESSION 或 session 文件 → 可 curl 切换
```

### install.sh 合并策略
- settings.json hooks 是数组，append 新条目
- `python3 -c` 做 JSON 合并（幂等：检查 command 字符串已存在则跳过）
- `--uninstall` 反向移除

### 上下文保护
- `cr()` 函数中 `export CLAUDE_CODE_DISABLE_1M_CONTEXT=1`，统一 200K

## 现有代码引用

| 文件 | 作用 | 复用方式 |
|------|------|---------|
| `~/cr-proxy.py` | 工作原型 | 演进为 proxy.py |
| `~/claude-session-usage.py` | 用量统计 | 改造为 tools/usage-stats.py |
| `~/fix-session-thinking-blocks.py` | 签名修复 | 直接复制 |
| `~/work/clawd-on-desk/hooks/clawd-hook.js` | Hook 模式 | 参考 stdin 解析 |
| `~/work/MuYu/.claude/skills/codex-plan-review/` | Skill 格式 | 参考 SKILL.md 结构 |
