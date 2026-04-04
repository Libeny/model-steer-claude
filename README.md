<p align="center">
  <h1 align="center">Model Steer Claude (MSC)</h1>
  <p align="center">
    <strong>模型方向盘 · 让 Claude Code / Claw 自己选模型</strong>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
    <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Claude_Code-compatible-orange.svg" alt="Claude Code">
  </p>
</p>

---

## 什么是 MSC？ / What is MSC?

**中文：** Claude Code 不允许你在会话中途切换模型。MSC 通过一个本地代理解决了这个问题——让 AI 根据任务复杂度自动选择模型：简单聊天走 GLM（几分钱一次），复杂编码走 Sonnet，架构设计走 Opus。你什么都不用管，它自己决定。

**English:** Claude Code doesn't let you switch models mid-session. MSC fixes this with a local proxy that lets the AI autonomously choose the right model based on task complexity: casual chat routes to GLM (pennies per request), complex coding to Sonnet, and deep architecture work to Opus. You don't have to do anything — the AI decides for itself.

---

## 为什么用 MSC？ / Why MSC?

| 没有 MSC | 有 MSC |
|----------|--------|
| 每条消息都用 Opus，$0.06/次 | 简单任务用 GLM，$0.002/次 |
| 无法中途换模型 | 同一 session 内自由切换 |
| API 挂了就停 | 自动降级，继续工作 |
| 盲猜用量 | Dashboard 实时查看 |

---

## 架构 / Architecture

```
Claude Code ──→ MSC Proxy (:3457) ──→ GLM       (Level 1, cheap, default)
                     │                 → Sonnet   (Level 2, powerful)
                     │                 → Opus     (Level 3, maximum)
                     │
              ┌──────┴──────┐
              │  SQLite DB  │        Fallback: down first, then up
              │  Session    │        失败先降级（省钱），再升级（保底）
              │  State      │
              └──────┬──────┘
                     │
         ┌───────────┼───────────┐
         │           │           │
   SessionStart   CLAUDE.md   Skills
   Hook           (决策规则)   /smoke
   CLAUDE_ENV_                 /redbull
   FILE                       /think-level
```

---

## 核心功能 / Features

- **AI 自主决策** — 模型根据任务复杂度自动选择等级，写在 CLAUDE.md 里的规则让 AI 自己判断
- **成本优化** — 简单任务用 GLM（约 ¥0.02/次），复杂任务才上 Opus，混合使用节省 30%+
- **无缝切换** — 同一 session 内切换模型，自动修复 thinking block 签名，不会中断对话
- **可视化面板** — Web Dashboard 配置模型、查看 session、监控用量
- **故障降级** — 请求失败自动 fallback（成本优先：先降级再升级）
- **Skill 支持** — `/smoke`（省钱模式）`/redbull`（全力模式）`/think-level 1|2|3`
- **零侵入** — 通过 `ANTHROPIC_BASE_URL` + Hook 注入，不修改 Claude Code 任何代码

---

## 快速开始 / Quick Start

### 前置条件 / Prerequisites

- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- GLM API Key（从 [智谱开放平台](https://open.bigmodel.cn/) 获取）
- Anthropic API Key 或 OAuth（Claude Code 自带）

### 安装 / Install

```bash
git clone https://github.com/Libeny/model-steer-claude.git
cd model-steer-claude
pip install httpx  # 唯一依赖

# 一键安装（core + hooks + skills + shell）
./install.sh

# 或者分步安装
./install.sh --core      # 代理 + 配置
./install.sh --hooks     # SessionStart / UserPromptSubmit hooks
./install.sh --skills    # /smoke, /redbull, /think-level
./install.sh --shell     # cr() 函数到 .zshrc
```

### 配置 API Key / Configure

```bash
# 编辑配置文件，填入你的 GLM Key
vim ~/.msc/config.json
```

把 `YOUR_GLM_KEY_HERE` 替换成你的真实 key。Anthropic 的 key 通过 Claude Code 自动透传，无需额外配置。

### 启动 / Run

```bash
# 用 cr 命令启动（自动管理代理进程）
cr

# 或者手动启动
python3 proxy.py &
ANTHROPIC_BASE_URL=http://127.0.0.1:3457 claude
```

启动后，AI 会自动根据任务复杂度在 GLM / Sonnet / Opus 之间切换。你也可以手动控制：

```
> /smoke          # 切到 Level 1 (GLM) — 省钱
> /redbull        # 切到 Level 3 (Opus) — 全力
> /think-level 2  # 切到 Level 2 (Sonnet)
```

---

## 工作原理 / How It Works

```
用户输入 `cr`
  │
  ├─ 1. cr() 检查代理是否运行，未运行则自动启动 proxy.py
  ├─ 2. 设置 ANTHROPIC_BASE_URL=http://127.0.0.1:3457
  ├─ 3. 启动 Claude Code
  │
  ├─ 4. SessionStart Hook 触发
  │     ├─ 从 stdin 读取 session_id
  │     ├─ echo "export CR_SESSION=xxx" >> $CLAUDE_ENV_FILE  (注入环境变量)
  │     ├─ curl /register?session=xxx  (注册到代理)
  │     └─ stdout 输出 "[msc] Session: xxx | Level 1 (GLM)"
  │
  ├─ 5. AI 加载 CLAUDE.md，了解路由规则
  │
  ├─ 6. 用户发送消息
  │     ├─ AI 评估任务复杂度
  │     ├─ 如需升级：curl /think-level?session=$CR_SESSION&level=2
  │     └─ 如需降级：curl /think-level?session=$CR_SESSION&level=1
  │
  ├─ 7. 代理收到 /v1/messages 请求
  │     ├─ 查 SQLite 获取当前 session level
  │     ├─ 路由到对应 provider (GLM / Anthropic)
  │     ├─ GLM: 替换 auth header + model 字段
  │     ├─ Anthropic: 透传 OAuth header + 修复 thinking 签名
  │     └─ 失败则 fallback: 当前 → 降级 → 升级 → 502
  │
  └─ 8. 响应返回给 Claude Code（签名已自动修复）
```

### 关键机制

**Session 传递 — CLAUDE_ENV_FILE：** SessionStart hook 通过 Claude Code 的 `CLAUDE_ENV_FILE` 机制将 `$CR_SESSION` 注入环境变量。后续所有 Bash 命令自动可用，无需文件回退或 ID 映射。

**签名修复 — 双向 Patching：** GLM 返回的 thinking block 签名无效，直接传给 Anthropic 会报错。MSC 在请求方向修复历史消息中的空签名，在响应方向修复 GLM 新返回的签名（支持跨 chunk 的流式修复）。

**Fallback — 成本优先：** 请求失败时，先在同 provider 重试 3 次，然后向下降级（找更便宜的），最后才向上升级（最后手段）。

---

## 配置 / Configuration

配置文件位于 `~/.msc/config.json`，默认模板在 `config/default-config.json`。

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `port` | int | 代理监听端口，默认 `3457` |
| `proxy` | string | 上游 HTTP 代理（如 `http://127.0.0.1:7897`），可选 |
| `default_level` | int | 新 session 默认级别，`1`-`3` |
| `levels` | object | 级别定义，key 为数字字符串 |
| `levels.N.name` | string | 级别名称（`glm` / `sonnet` / `opus`） |
| `levels.N.provider` | string | 使用的 provider 名称 |
| `levels.N.model` | string | 实际模型 ID |
| `providers` | object | Provider 配置 |
| `providers.X.url` | string | API 端点 URL |
| `providers.X.key` | string | API Key（GLM 需要） |
| `providers.X.passthrough_auth` | bool | 是否透传 Claude Code 的 Authorization header |
| `retry.max_attempts` | int | 每个 level 的最大重试次数 |
| `retry.interval_seconds` | int | 重试间隔（秒） |

### 示例配置

**默认（GLM + Anthropic）：**

```json
{
  "port": 3457,
  "proxy": "http://127.0.0.1:7897",
  "default_level": 1,
  "levels": {
    "1": {"name": "glm", "provider": "glm", "model": "glm-5.1"},
    "2": {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-4-6"},
    "3": {"name": "opus", "provider": "anthropic", "model": "claude-opus-4-6"}
  },
  "providers": {
    "glm": {"url": "https://open.bigmodel.cn/api/anthropic/v1/messages", "key": "YOUR_GLM_KEY_HERE"},
    "anthropic": {"url": "https://api.anthropic.com", "passthrough_auth": true}
  },
  "retry": {"max_attempts": 3, "interval_seconds": 2}
}
```

**纯 Anthropic（Haiku + Sonnet + Opus）：**

```json
{
  "port": 3457,
  "default_level": 1,
  "levels": {
    "1": {"name": "haiku", "provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    "2": {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-4-6"},
    "3": {"name": "opus", "provider": "anthropic", "model": "claude-opus-4-6"}
  },
  "providers": {
    "anthropic": {"url": "https://api.anthropic.com", "passthrough_auth": true}
  },
  "retry": {"max_attempts": 3, "interval_seconds": 2}
}
```

**无代理（直连）：**

```json
{
  "port": 3457,
  "default_level": 1,
  "levels": {
    "1": {"name": "glm", "provider": "glm", "model": "glm-5.1"},
    "2": {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-4-6"}
  },
  "providers": {
    "glm": {"url": "https://open.bigmodel.cn/api/anthropic/v1/messages", "key": "YOUR_GLM_KEY_HERE"},
    "anthropic": {"url": "https://api.anthropic.com", "passthrough_auth": true}
  },
  "retry": {"max_attempts": 3, "interval_seconds": 2}
}
```

---

## 控制面板 / Dashboard

![Dashboard](docs/dashboard.png)

代理运行后，访问 [http://127.0.0.1:3457/ui](http://127.0.0.1:3457/ui) 打开控制面板。

**功能：**
- 活跃 Session 列表，显示当前级别和 token 用量
- 模型配置在线查看
- 实时状态刷新（每 5 秒自动更新）
- 零依赖单文件 HTML，内嵌 CSS + JS

---

## Skills / 技能

MSC 提供三个 Claude Code Skill，在对话中通过斜杠命令触发：

### `/smoke` — 省钱模式

切换到 Level 1 (GLM)。适用于任务完成、对话收尾、简单问答。

```
> /smoke
[msc] Switched to Level 1 (GLM)
```

### `/redbull` — 全力模式

切换到 Level 3 (Opus)。适用于架构设计、深度分析、复杂调试。

```
> /redbull
[msc] Switched to Level 3 (Opus)
```

### `/think-level` — 指定级别

切换到任意级别。

```
> /think-level 2
[msc] Switched to Level 2 (Sonnet)
```

除了手动 Skill，AI 也会通过 CLAUDE.md 中的决策规则自动切换——你完全可以不管它。

---

## 工具 / Tools

### usage-stats.py — 用量统计

分析 Claude Code session 的 token 用量和费用，支持模糊搜索。

```bash
# 模糊搜索 session（输入部分 ID 即可）
python3 tools/usage-stats.py e664

# 分析全部 session
python3 tools/usage-stats.py --all

# 最近 5 个 session
python3 tools/usage-stats.py --all --recent 5

# 按模型筛选
python3 tools/usage-stats.py --all --model glm-5.1

# JSON 格式输出
python3 tools/usage-stats.py --all --format json
```

### fix-thinking-blocks.py — 签名修复

修复 session 文件中无效的 thinking block 签名（从 GLM 切换到 Claude 时可能产生）。

```bash
# 分析（不修改）
python3 tools/fix-thinking-blocks.py session.jsonl

# 修复（替换无效签名，保留思考内容）
python3 tools/fix-thinking-blocks.py session.jsonl --fix

# 修复（删除无效 thinking block）
python3 tools/fix-thinking-blocks.py session.jsonl --fix --mode delete
```

---

## 省钱效果 / Cost Savings

实测数据（9 轮对话，混合编码 + 问答任务）：

```
MSC 混合模式:
  GLM   7 条  $0.034
  Opus  2 条  $0.366
  合计        $0.400

纯 Claude 模式:
  Opus  9 条  $0.581

节省: $0.181 (31%)
```

日常使用中，大部分对话是简单的文件读取、git 操作、问答——这些走 GLM 只需几分钱。只有真正需要深度思考的任务才会升级到 Sonnet/Opus。

---

## 项目结构 / Project Structure

```
model-steer-claude/
├── proxy.py                     # 核心代理（HTTP server + SQLite + 签名修复）
├── install.sh                   # 分步安装器（--core --hooks --skills --shell --uninstall）
├── CLAUDE.md                    # AI 自动路由决策规则
├── hooks/
│   ├── session-start.sh         # SessionStart hook — 注入 CR_SESSION + 注册代理
│   └── user-prompt-submit.sh    # UserPromptSubmit hook — 显示当前级别
├── skills/
│   ├── smoke/SKILL.md           # /smoke → Level 1 (GLM)
│   ├── redbull/SKILL.md         # /redbull → Level 3 (Opus)
│   └── think-level/SKILL.md     # /think-level N
├── ui/
│   └── dashboard.html           # Web 控制面板（零依赖单文件）
├── tools/
│   ├── usage-stats.py           # 用量统计 + 费用计算
│   └── fix-thinking-blocks.py   # thinking 签名修复
├── config/
│   └── default-config.json      # 默认配置模板
└── .gitignore
```

---

## 路线图 / Roadmap

- [x] 核心代理 + SQLite 持久化
- [x] SessionStart hook + CLAUDE_ENV_FILE 注入
- [x] Web Dashboard 控制面板
- [x] Skills（/smoke, /redbull, /think-level）
- [x] 用量统计工具（模糊搜索 + 费用计算）
- [x] Thinking block 签名修复（代理实时 + 离线工具）
- [ ] CoT Viewer（Chain of Thought 可视化）
- [ ] Trigger Rules UI（自定义模型切换条件）
- [ ] Multi-provider 健康监控
- [ ] 用量分析报表（日报 / 周报）

---

## 卸载 / Uninstall

```bash
./install.sh --uninstall
```

会清理：hooks、skills、cr() 函数、配置目录。

---

## Contributing

欢迎贡献！请：

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/my-feature`)
3. 提交改动 (`git commit -m 'Add my feature'`)
4. 推送分支 (`git push origin feature/my-feature`)
5. 创建 Pull Request

---

## License

[MIT](LICENSE)
