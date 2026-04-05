<p align="center">
  <h1 align="center">Model Steer Claude (MSC)</h1>
  <p align="center">
    <strong>模型方向盘 · 让 Claude Code 自己选模型，让你的钱包喘口气</strong>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
    <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Claude_Code-compatible-orange.svg" alt="Claude Code">
    <img src="https://img.shields.io/badge/savings-30%25+-brightgreen.svg" alt="Cost Savings 30%+">
  </p>
</p>

---

## 故事的开始

我在用 Claude Code 搭建一个数字化项目时，发现了一个让人心痛的事实——问一句"这个文件在哪"也在烧 Opus 的 token。看了一下 git status？Opus。查了一下报错？Opus。闲聊了两句？还是 Opus。一天下来，光是这些杂活就花了 $5+。

我想：**简单任务用便宜模型（GLM，¥0.02/次），复杂任务再上 Opus（$0.06/次），这不就能省 80% 的钱吗？**

但 Claude Code 不支持在 session 内切换模型。

更麻烦的是，当我尝试手动切换时，发现了一个致命问题——

```
API Error: 400 {"type":"error","error":{"type":"invalid_request_error",
"message":"messages.1.content.0: Invalid `signature` in `thinking` block"}}
```

Anthropic 的 thinking block 签名机制会导致跨模型会话**永久损坏**。GLM 产生的空签名，切回 Claude 时直接 400 报错，session 彻底废掉，连 `/branch` 都救不回来。

于是我花了几天时间逆向了 Anthropic 的签名协议，发现了一个关键事实：**signature 只做格式校验，不校验内容匹配**。也就是说，用一个合法的 placeholder 签名就能打通跨模型会话。

最终方案：不改 Claude Code 一行代码，通过外层网关统一路由，模型选择与 Claude Code 完全解耦。AI 自己决定该用什么等级的模型——该省省，该花花。

**Your AI just learned to manage its own budget.**

---

## 什么是 MSC？

一句话：**MSC 是一个本地代理，让 Claude Code 在同一个 session 内自由切换模型。**

简单聊天走 GLM（几分钱），复杂编码走 Sonnet，架构设计走 Opus。AI 自己判断任务复杂度，自己决定用哪个模型。你什么都不用管。

| 没有 MSC | 有 MSC |
|----------|--------|
| 每条消息都用 Opus，$0.06/次 | 简单任务用 GLM，$0.002/次 |
| 无法中途换模型 | 同一 session 内自由切换 |
| 跨模型切换 session 损坏 | 自动修复签名，无缝切换 |
| API 挂了就停 | 自动降级，继续工作 |
| 盲猜用量 | Dashboard 实时监控 |

---

## /smoke 和 /redbull

除了 AI 自动决策，你也可以手动控制。我们为此设计了两个很有性格的命令：

### /smoke — "来根烟，歇会儿"

```
> /smoke
[msc] Switched to Level 1 (GLM)
```

任务做完了？来根烟，切到省钱模式。GLM 接手闲聊，Opus 去休息。你的钱包会感谢你。

适用于：任务收尾、简单问答、文件查找、git 操作。

### /redbull — "灌一罐，狂暴模式"

```
> /redbull
[msc] Switched to Level 3 (Opus)
```

遇到硬骨头？灌一罐红牛，Opus 全力输出，不计成本。架构设计、复杂 debug、多步推理——该上就上。

适用于：架构设计、深度分析、复杂调试、大规模重构。

### /think-level — 精确控制

```
> /think-level 2
[msc] Switched to Level 2 (Sonnet)
```

觉得 GLM 不够用、Opus 又太贵？Sonnet 正好。三个级别随意切换。

---

## 背景：从踩坑到解决

### Phase 1：发现问题——钱烧得太快

用 Claude Code 搭项目，大部分对话是"这个文件在哪"、"运行一下 git status"、"帮我改个 typo"这种简单任务。但每一条都在烧 Opus 的 token。混合使用便宜模型能省下大量成本，但 Claude Code 不支持 session 内切换模型。

### Phase 2：遇到阻塞——签名不兼容

手动切换模型后，发现 Anthropic 的 thinking block 签名机制是个拦路虎：

- Anthropic Claude 的 extended thinking 会产生加密签名（356-2344 chars 的 Base64 + Protobuf）
- GLM、DeepSeek 等非 Anthropic 模型产生的签名为空
- 从 GLM 切回 Claude 时，Anthropic API 拒绝空签名 → 400 错误
- 一旦损坏，session 无法恢复

为了解决这个问题，我逆向了 Anthropic 的签名协议：

```
Signature = Base64( Protobuf {
    Field 1 (元数据): 版本号 + 加密算法 + nonce + 模型名
    Field 2: IV/salt (12 bytes)
    Field 3: auth tag (12 bytes)
    Field 4: MAC/HMAC (48 bytes)
    Field 5: AES-GCM 加密的 thinking 原文
})
```

然后通过一系列实验验证了三个关键事实：

1. **Signature 是格式校验，不是内容校验** —— API 检查签名格式合法即可，不校验 signature 与 thinking 文本的对应关系
2. **Thinking 文本从明文读取** —— API 读的是 `thinking` 字段的明文，不是通过解密 signature 获取。用 placeholder 签名替换空签名后，Claude 仍能读到非 Anthropic 模型的完整推理过程
3. **签名无法伪造，但可以替换** —— 加密密钥在 Anthropic 服务端，无法为 GLM 的 thinking 生成合法签名。但用一个已知的 valid placeholder 替换空签名就够了

### Phase 3：架构解耦——让网关来处理

核心思路：**模型选择与 Claude Code 解耦。**

不通过 Base URL + Token 直接指定模型，而是统一路由到外层网关。Claude Code 启动前就完成配置解耦——它以为自己在和 Anthropic API 对话，实际上请求经过了 MSC 代理，被路由到了正确的模型。

签名问题？代理在两个方向自动处理：
- **请求方向**：`fix_signatures()` 修复历史消息中的无效签名
- **响应方向**：`StreamSignaturePatcher` 实时修补 GLM 返回的流式响应

AI 自主决策？通过 CLAUDE.md 注入路由规则，AI 评估任务复杂度后自行调用 proxy API 切换模型等级。

**零侵入，零修改，零感知。**

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
   SessionStart   CLAUDE.md   Skill + Commands
   Hook           (决策规则)   /smoke
   CLAUDE_ENV_                 /redbull
   FILE                       /think-level
```

---

## 核心功能 / Features

- **AI 自主决策** — 写在 CLAUDE.md 里的规则让 AI 自己判断任务复杂度，自动选择模型等级
- **成本优化** — 简单任务用 GLM（约 ¥0.02/次），复杂任务才上 Opus，混合使用节省 30%+
- **签名双向修复** — 请求方向修复历史签名，响应方向实时修补 GLM 流式输出，跨 chunk 安全
- **无缝切换** — 同一 session 内自由切换模型，不会中断对话
- **可视化面板** — Web Dashboard 查看 session、监控用量、配置模型
- **故障降级** — 请求失败自动 fallback（成本优先：先降级再升级）
- **1 Skill + 3 Commands** — AI 自动决策 + 手动 /smoke /redbull /think-level
- **零侵入** — 通过 `ANTHROPIC_BASE_URL` + Hook 注入，不修改 Claude Code 任何代码

---

## 快速开始 / Quick Start

### 前置条件

- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- GLM API Key（从 [智谱开放平台](https://open.bigmodel.cn/) 获取）
- Anthropic API Key 或 OAuth（Claude Code 自带）

### 安装

```bash
git clone https://github.com/Libeny/model-steer-claude.git
cd model-steer-claude
pip install httpx  # 唯一依赖

# 一键安装（core + hooks + skills + shell）
./install.sh

# 或者分步安装
./install.sh --core      # 代理 + 配置
./install.sh --hooks     # SessionStart / UserPromptSubmit hooks
./install.sh --skills    # Skill + Commands
./install.sh --shell     # cr() 函数到 .zshrc
```

### 配置 API Key

```bash
vim ~/.msc/config.json
```

把 `YOUR_GLM_KEY_HERE` 替换成你的真实 key。Anthropic 的 key 通过 Claude Code 自动透传，无需额外配置。

### 启动

```bash
# 用 cr 命令启动（自动管理代理进程）
cr

# 或者手动启动
python3 proxy.py &
ANTHROPIC_BASE_URL=http://127.0.0.1:3457 claude
```

启动后 AI 会自动选择模型。你也可以随时手动控制：

```
> /smoke          # 来根烟，切到省钱模式
> /redbull        # 灌一罐，切到狂暴模式
> /think-level 2  # 精确切到 Sonnet
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
  │     ├─ echo "export CR_SESSION=xxx" >> $CLAUDE_ENV_FILE
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
  │     ├─ Anthropic: 透传 OAuth header + fix_signatures() 修复历史签名
  │     ├─ GLM 响应: StreamSignaturePatcher 实时修补签名
  │     └─ 失败则 fallback: 当前 → 降级 → 升级 → 502
  │
  └─ 8. 响应返回给 Claude Code（签名已自动修复）
```

### 关键机制

**Session 传递 — CLAUDE_ENV_FILE：** SessionStart hook 通过 `CLAUDE_ENV_FILE` 将 `$CR_SESSION` 注入环境变量。后续所有 Bash 命令自动可用，无需文件回退或 ID 映射。

**签名修复 — 双向 Patching：** 请求方向 `fix_signatures()` 扫描对话历史中的无效签名并替换为 placeholder；响应方向 `StreamSignaturePatcher` 实时修补 GLM 新返回的流式签名（支持跨 chunk buffer 安全）。

**Fallback — 成本优先：** 请求失败时先在同 provider 重试 3 次，然后向下降级（找更便宜的），最后才向上升级（最后手段）。

---

## 1M Context 的坑

`cr()` 函数默认设置 `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`，把所有模型的上下文窗口限制在 200K。

**为什么？** Opus 支持 1M context，但 GLM 和 Sonnet 不支持。如果 Opus 消耗了超过 200K 的上下文，切到 GLM/Sonnet 时就会因上下文过长而失败。统一限制在 200K 才能保证模型间的无缝切换。

如果你的配置中所有级别都用 Opus（不差钱的话），可以移除这个限制。

---

## Skill + Commands

MSC 采用 **1 Skill + 3 Commands** 的模式：

### Skill：`model-steer`（自动挡）

位于 `skills/model-steer/SKILL.md`。AI 在每轮对话前自动评估任务复杂度，自己调 proxy API 切换模型等级。你不需要手动调用，AI 自己决定——就像自动挡变速箱。

### Commands（手动挡）

位于 `commands/` 目录，3 个斜杠命令供你手动干预：

| 命令 | 效果 | 场景 |
|------|------|------|
| `/smoke` | Level 1 (GLM) | 任务完成、闲聊、省钱 |
| `/redbull` | Level 3 (Opus) | 硬骨头、架构、深度分析 |
| `/think-level N` | Level N | 精确控制 |

**两者的关系：** Skill 是自动挡——AI 根据 CLAUDE.md 规则自主切换，你无感知。Commands 是手动挡——你主动覆盖 AI 的决策，优先级最高。

日常使用中完全可以不管，AI 会做出合理决策。只有需要强制指定时才拉手动挡。

---

## 控制面板 / Dashboard

![Dashboard](docs/dashboard.png)

代理运行后访问 [http://127.0.0.1:3457/ui](http://127.0.0.1:3457/ui)。

- 活跃 Session 列表，显示当前级别和 token 用量
- 模型配置在线查看
- 实时状态刷新（每 5 秒自动更新）
- 零依赖单文件 HTML，内嵌 CSS + JS

---

## 工具 / Tools

### usage-stats.py — 你到底花了多少钱

分析 Claude Code session 的 token 用量和费用，支持模糊搜索。

```bash
python3 tools/usage-stats.py e664        # 模糊搜索 session
python3 tools/usage-stats.py --all       # 全部 session
python3 tools/usage-stats.py --all --recent 5     # 最近 5 个
python3 tools/usage-stats.py --all --model glm-5.1 # 按模型筛选
python3 tools/usage-stats.py --all --format json   # JSON 输出
```

### fix-thinking-blocks.py — 离线签名修复

通常由 proxy 自动处理，此工具用于手动修复已损坏的 session 文件。

```bash
python3 tools/fix-thinking-blocks.py session.jsonl          # 分析（不修改）
python3 tools/fix-thinking-blocks.py session.jsonl --fix     # 修复（placeholder 替换）
python3 tools/fix-thinking-blocks.py session.jsonl --fix --mode delete  # 修复（删除）
```

---

## 省了多少钱？ / Cost Savings

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

日常使用中，大部分对话是简单的文件读取、git 操作、问答——这些走 GLM 只需几分钱。只有真正需要深度思考时才升级到 Sonnet/Opus。

---

## 配置 / Configuration

配置文件 `~/.msc/config.json`，默认模板在 `config/default-config.json`。

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

## 项目结构 / Project Structure

```
model-steer-claude/
├── proxy.py                     # 核心代理（HTTP server + SQLite + 签名修复）
├── install.sh                   # 分步安装器（--core --hooks --skills --shell --uninstall）
├── CLAUDE.md                    # AI 自动路由决策规则
├── hooks/
│   ├── session-start.sh         # CLAUDE_ENV_FILE 注入 $CR_SESSION
│   └── user-prompt-submit.sh    # 注入当前 level 到 prompt
├── skills/
│   └── model-steer/SKILL.md     # AI 自动决策逻辑（自动挡）
├── commands/
│   ├── smoke.md                 # /smoke → Level 1
│   ├── redbull.md               # /redbull → Level 3
│   └── think-level.md           # /think-level N
├── ui/
│   └── dashboard.html           # Web Dashboard（零依赖单文件）
├── tools/
│   ├── usage-stats.py           # 用量统计 + 费用计算（模糊搜索）
│   └── fix-thinking-blocks.py   # thinking 签名离线修复工具
├── config/
│   └── default-config.json      # 默认配置模板
└── .gitignore
```

---

## 路线图 / Roadmap

- [x] 核心代理 + SQLite 持久化
- [x] SessionStart hook + CLAUDE_ENV_FILE 注入
- [x] Web Dashboard 控制面板
- [x] Skill（自动挡）+ Commands（/smoke, /redbull, /think-level）
- [x] 用量统计工具（模糊搜索 + 费用计算）
- [x] Thinking block 签名双向实时修复 + 离线修复工具
- [x] 跨模型 thinking 签名逆向分析与实验验证
- [ ] CoT Viewer（Chain of Thought 可视化）
- [ ] Trigger Rules UI（自定义模型切换条件）
- [ ] Multi-provider 健康监控
- [ ] 用量分析报表（日报 / 周报）

---

## 卸载 / Uninstall

```bash
./install.sh --uninstall
```

会清理：hooks、skills、commands、cr() 函数、配置目录。

---

## Contributing

欢迎贡献！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/my-feature`)
3. 提交改动 (`git commit -m 'Add my feature'`)
4. 推送分支 (`git push origin feature/my-feature`)
5. 创建 Pull Request

---

## License

[MIT](LICENSE)
