# claude-code-claw — 智能模型路由代理

## Context

用户已有一个可工作的原型（`~/cr-proxy.py`），实现了 Claude Code 请求在 GLM（便宜）和 Anthropic Sonnet/Opus（强大）之间的路由切换。现在要把它做成开源项目 `~/work/claude-code-claw/`，加入自动决策、降级链、持久化、skill 等完整功能。

## 项目结构

```
claude-code-claw/
├── proxy.py                     # 核心代理：路由 + 降级 + 重试 + 签名修复
├── install.sh                   # 一键安装
├── hooks/
│   ├── session-start.js         # 捕获 session_id，注册到 proxy
│   └── user-prompt-submit.js    # 注入当前模型/effort 信息到 prompt
├── skills/
│   ├── think-harder/SKILL.md    # /think-harder 升级模型
│   └── think-cheaper/SKILL.md   # /think-cheaper 降级模型
├── CLAUDE.md                    # 项目级自动路由指令（安装时复制到目标项目）
├── tools/
│   ├── usage-stats.py           # 用量统计（支持模糊搜索 session_id）
│   └── fix-thinking-blocks.py   # thinking 签名修复
├── config/
│   └── default-config.json      # 默认配置模板
├── README.md
└── .gitignore
```

## 实现计划

### Phase 1：Core（核心可用）

**1. `config/default-config.json`** — 配置模板
```json
{
  "port": 3457,
  "proxy": "http://127.0.0.1:7897",
  "default_model": "glm",
  "providers": {
    "glm": { "url": "...", "key": "YOUR_KEY", "model": "glm-5.1" },
    "anthropic": { "url": "https://api.anthropic.com", "passthrough_auth": true }
  },
  "fallback_chain": ["opus", "sonnet", "glm"],
  "retry": { "max_attempts": 3, "interval_seconds": 2 },
  "effort_default": "medium"
}
```

**2. `proxy.py`** — 从 `~/cr-proxy.py` 演进，~350 行
- 启动时加载 `~/.claude-code-claw/config.json`
- 多 provider 支持（每个 provider 独立 httpx.Client）
- 降级链：请求失败 → 重试 3 次（2s 间隔）→ fallback 到下一优先级
- 优先级：Opus → Sonnet → GLM → 其他
- 新端点：`/register?session=X`、`/use-opus`、`/status`、`/stats`
- Session 路由持久化：`~/.claude-code-claw/routes.json`
- 保留全部签名修复逻辑（StreamSignaturePatcher + fix_signatures + patch_json_signatures）
- Auth：GLM 替换 key，Anthropic 透传全部 header（OAuth 兼容）

**3. `hooks/session-start.js`** — ~50 行
- 参考 `~/work/clawd-on-desk/hooks/clawd-hook.js` 的 stdin 解析模式
- 读 stdin JSON → 提取 session_id → `curl /register?session=<id>` → 写 `/tmp/cr-session`
- 400ms stdin 超时 + 500ms HTTP 超时 = 900ms < 1s 预算

**4. `CLAUDE.md`** — 自动路由指令
- 读 session_id：`cat /tmp/cr-session` 或 `$CR_SESSION`
- 决策规则：简单→GLM，代码→Sonnet，架构→Opus
- curl 切换命令（/use-glm, /use-sonnet, /use-opus）
- 规则：响应前执行 curl，不向用户提及切换

**5. `.gitignore`**

### Phase 2：Integration（串联）

**6. `install.sh`** — ~120 行
- 创建 `~/.claude-code-claw/`，复制默认配置
- 向 `.zshrc` 添加 `cr()` 函数（含 `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`）
- 向 `~/.claude/settings.json` **合并**（非覆盖）SessionStart + UserPromptSubmit hook
- 安装 skill 文件到 `~/.claude/skills/`
- 不自动启动 proxy

**7. `hooks/user-prompt-submit.js`** — ~40 行
- 查询 `/status?session=X` 获取当前模型
- stdout 输出 `[claw: GLM | effort: medium]` 注入到 prompt

**8. Skills 体系**

| Skill | 命令 | 作用 |
|-------|------|------|
| `skills/smoke/SKILL.md` | `/smoke` | 降到默认模型（GLM），对话结束/空闲时自动触发 |
| `skills/redbull/SKILL.md` | `/redbull` | 升到最高模型（Opus），全力以赴 |
| `skills/think-level/SKILL.md` | `/think-level 1\|2\|3` | 精确指定等级：1=GLM, 2=Sonnet, 3=Opus |

每个 skill 内部：读 session_id → 查 /status → curl 切换 → 确认当前等级

**CLAUDE.md 管决策（何时切），skill 管执行（怎么切）：**
- CLAUDE.md：复杂任务自动升级，对话结束/任务完成时自动 `/smoke` 降级
- Skill：具体 curl 命令和确认消息

### Phase 3：Polish

**10. `tools/fix-thinking-blocks.py`** — 直接从 `~/fix-session-thinking-blocks.py` 复制

**11. `tools/usage-stats.py`** — 从 `~/claude-session-usage.py` 改造
- 新增：模糊搜索 session_id（部分匹配 UUID）
- 新增：查询 proxy `/stats` 端点获取实时数据

**12. `README.md`**

## 关键技术细节

### 降级重试逻辑
```
请求 GLM → 失败 → 重试 3 次（间隔 2s）
                  → 仍失败 → fallback Sonnet → 重试 3 次
                  → 仍失败 → fallback Opus → 重试 3 次
                  → 全部失败 → 返回 502
```

### Auth 路由
- GLM：去掉原始 auth header，注入 GLM key
- Anthropic：透传全部 header（OAuth Bearer token 直通）

### 签名修复（双向）
- 请求方向：`fix_signatures(messages)` 修复历史中 GLM 的空签名
- 响应方向：`StreamSignaturePatcher` 修复 GLM 新返回的空签名
- 跨 chunk 安全：buffer 不完整的 SSE 行

### Hook 时序
```
claude 启动 → 生成 session_id → SessionStart hook 触发
  → hook 读 stdin 拿 session_id → 注册到 proxy + 写 /tmp/cr-session
  → hook stdout 注入: "[claw] Session: xxx | Level 1 (GLM)"
  → "Available: /think-level 1|2|3 | /smoke | /redbull"
  → 模型加载 → 读 CLAUDE.md + 看到 claw 状态 → 用户首条消息
  → 模型读 /tmp/cr-session → 可以 curl 切换
```

### SessionStart 注入内容
hook stdout 输出以下内容，自动注入模型上下文：
```
[claw] Session registered: <session_id>
Current: GLM (Level 1) | Available: /think-level 1 (GLM) | 2 (Sonnet) | 3 (Opus)
Shortcuts: /smoke (→ GLM) | /redbull (→ Opus)
Auto-rules: complex task → auto upgrade | task done → auto /smoke
```

### install.sh 合并策略
- settings.json hooks 是数组结构，append 新条目，不覆盖
- 用 `python3 -c` 做 JSON 合并（bash 原生操作不可靠）
- 幂等：重复运行不会重复添加

## 验证方式

1. `python3 proxy.py` → `curl http://127.0.0.1:3457/` 确认启动
2. `curl -X POST /v1/messages` 测试 GLM 路由
3. `curl /use-sonnet?session=test` → 再次 POST → 确认走 Sonnet
4. 模拟 GLM 失败 → 确认自动 fallback 到 Sonnet
5. `cr` 交互模式 → 验证 SessionStart hook 注册 session_id
6. `/think-harder` → 确认模型切换
7. `python3 tools/usage-stats.py <partial-session-id>` → 确认模糊搜索
