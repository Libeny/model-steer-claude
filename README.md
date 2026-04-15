<p align="center">
  <img src="docs/sailor.png" width="180" alt="MSC 船长">
  <h1 align="center">NB-Claude</h1>
  <p align="center">
    <strong>Never Break Your Flow</strong>
  </p>
  <p align="center">
    <em>为 AI 数字员工 / AI-Coding 集群设计的 Claude Code 高可用模型代理</em>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
    <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Claude_Code-plugin-orange.svg" alt="Claude Code Plugin">
    <a href="README_EN.md">English</a>
  </p>
</p>

---

## 为什么需要 NB-Claude

Claude Code 是当下最强编程 Agent，但它有一条硬限制：**额度用完就停工**。

人在用的时候还好 — 429 了等一会儿，重新来。但对于 **AI 数字员工**来说，这是致命的：429 了整个任务链断了，后面的任务全部卡住，没人手动重启。

NB-Claude 解决这个问题。它是一个本地代理，架在 Claude Code 和模型 API 之间，确保 **任何情况下都有模型可用**：

| 场景 | 没有 NB-Claude | 有 NB-Claude |
|------|---------------|-------------|
| Sonnet 额度耗尽 | 任务中断，等待重置 | 自动切 GLM，无感继续 |
| API 返回 500 | Claude Code 退出 | 自动重试下一个 provider |
| GLM 临时限流 | 直接报错 | 自动降级到其他可用模型 |
| 主模型持续不可用 | 人工介入 | Circuit Breaker 自动熔断，5 分钟探测恢复 |

## 三层架构

NB-Claude 提供三个逐层增强的能力，按需启用：

```
┌──────────────────────────────────────────────────┐
│  第三层：Agent 编排（规划中）                        │
│  多 Agent 协作时，每个 Agent 分配最适配的模型          │
├──────────────────────────────────────────────────┤
│  第二层：任务路由（cr --route 开启）                  │
│  你定义规则，AI 按规则自动切换模型                      │
├──────────────────────────────────────────────────┤
│  第一层：Never Fallback（默认开启）                   │
│  主模型不可用时，自动降级到下一个可用模型                │
└──────────────────────────────────────────────────┘
```

---

## 第一层：Never Fallback（默认开启）

```
                          ┌─ Sonnet ── 429/500? ──┐
用户 → Claude Code → 代理 ─┤                        ├→ 永不中断
                          └─ GLM / DeepSeek / ... ─┘
```

所有 API 请求经过本地代理。当主模型不可用时，自动按你配置的链路降级：

```
Sonnet → GLM-5.1 → 其他可用模型
```

### 智能错误分类

不只是看 HTTP 状态码，还解析模型厂商的业务错误码，精准判断该不该 fallback：

| 错误 | 来源 | 处理 |
|------|------|------|
| 429 / 500 / 503 | Anthropic | **fallback** — 额度耗尽或服务异常 |
| 1302 / 1303 / 1305 / 1312 | GLM 临时限流 | **fallback** — 短期可恢复 |
| 1234 | GLM 网络错误 | **fallback** — 服务端网络问题 |
| 1301 | GLM 内容安全 | **不 fallback** — 换模型同样会触发 |
| 1304 / 1308 / GLM 配额耗尽 | **不 fallback** — 熔断该 provider |

错误规则完全可配置。`_default` 定义通用规则，per-provider 键定义厂商特定错误码。添加新厂商只需加一段配置。

### Circuit Breaker（熔断器）

当检测到配额耗尽（GLM 1304/1308、Anthropic 429 + quota API 确认），立即熔断该 provider：

- **熔断状态**：后续请求直接跳过该 provider，零延迟
- **自动恢复**：每 5 分钟探测一次，provider 恢复后自动解除熔断
- **Dashboard 可视**：实时查看哪些 provider 被熔断、原因、何时被熔断

---

## 第二层：任务路由（`cr --route` 开启）

**你定义规则，AI 按规则自动切换模型。**

默认 `cr` 只做 fallback 保障。加 `--route` 后，代理将路由规则注入 system prompt，AI 在每次回复前根据任务类型自动选择最合适的模型。

### 工作方式

```
用户提问 → AI 读取路由规则 → 判断任务类型 → curl 切换到对应等级 → 用该模型回答
```

### 规则完全由你定义

在 Dashboard 上为每个模型等级写**路由场景描述**，AI 根据这些描述做决策：

| 等级 | 模型 | 路由场景描述 | 示例任务 |
|------|------|------------|---------|
| 1 | GLM-5.1 | 闲聊、简单 Q&A、文件查找、定时任务 | "这个文件在哪？" |
| 2 | Sonnet | 代码开发、需求评审、测试、调试、重构 | "帮我写个单元测试" |
| 3 | Opus | 架构设计、深度代码审计、系统级决策 | "设计微服务架构" |
| N | 任意模型 | 你定义的场景 | 前端 UI 用视觉模型... |

**灵活之处**：

- 等级数量不限，模型不限 — 想加 DeepSeek、Moonshot、Qwen 随时加
- 每个等级的**路由场景**自由填写 — "定时任务和异步回调"、"前端 UI 开发"、"数据分析"...
- 拖拽排序调整优先级，保存后下次 session 立即生效
- 也支持手动切换：`/smoke`（最便宜）、`/redbull`（最强）、`/think-level N`

---

## 第三层：Agent 编排（规划中）

多 Agent 协作场景下，每个 sub-agent 可以使用最适合其任务的模型。例如：架构设计 Agent 用 Opus，编码 Agent 用 Sonnet，测试 Agent 用快速模型。与 Claude Code Agent SDK 深度集成。

---

## 额度查看

```bash
crq    # 查看当前所有模型的额度状态和重置时间
```

输出示例：

```
  Claude Subscription: pro

  5h         [█████████░░░░░░░░░░░]  45%  55% left    resets in 2h30m
  7d         [██████████████░░░░░░]  72%  28% left    resets in 6d12h
  7d Sonnet  [█████████████████░░░]  88%  12% left    resets in 6d12h

  ✓ glm: glm-5.1 (ok)
```

额度信息实时从 Anthropic usage API 获取，帮你合理安排工作时间窗口。

---

## 快速开始

```bash
git clone https://github.com/Libeny/model-steer-claude.git
cd model-steer-claude
bash install.sh

# 编辑配置 — 填入 API Key
vim ~/.msc/config.json

# 加载 shell 函数
source ~/.zshrc
cr                    # 启动 Claude Code（自动 fallback 保障）
cr --route            # 启动 + AI 按规则路由
crd                   # 打开 Dashboard
crq                   # 查看模型额度
```

## CLI 命令

```bash
cr                          # 交互式会话（仅 fallback 保障）
cr --route                  # 交互式会话（fallback + AI 按规则路由）
cr -p "解释这个文件"         # 单次输出
cr --resume <session-id>    # 恢复会话
crd                         # 打开 Dashboard
crq                         # 查看额度状态
```

会话内命令（`--route` 模式下可用）：

| 命令 | 效果 |
|------|------|
| `/smoke` | 切到最便宜模型 |
| `/redbull` | 切到最强模型 |
| `/think-level N` | 切到指定等级 |

## Agent SDK 模式

```python
from claude_agent_sdk import query, ClaudeAgentOptions
from pathlib import Path

MSC_PLUGIN = str(Path.home() / ".claude/plugins/msc")
ROUTING = (Path.home() / ".msc/routing-prompt.md").read_text()

async for msg in query(
    prompt="用 Python 实现红黑树，包含 insert/search/delete 和测试",
    options=ClaudeAgentOptions(
        plugins=[{"type": "local", "path": MSC_PLUGIN}],
        env={"MSC_ENABLED": "1"},
        system_prompt=ROUTING,
    ),
):
    print(msg.content)
```

同一个插件、同一套 hooks、同一套路由 — 和 `cr` 行为完全一致。

## Dashboard

运行 `crd` 打开本地 Dashboard `http://localhost:3457/ui`，所有配置通过界面完成，无需手编 JSON。

### 模型配置

管理模型等级、排序和路由场景：

<p align="center"><img src="docs/screenshot-models.png" width="700" alt="模型配置"></p>

### 开销面板

实时查看费用和节省：

<p align="center"><img src="docs/screenshot-cost.png" width="700" alt="开销面板"></p>

- **混合模式费用** — 实际花费（支持 ¥/$ 切换）
- **已为您节省** — 非 Claude 模型的 token 如果全走 Sonnet 要多花多少
- **模型用量分布** — 各模型 token 占比
- **项目用量排行** — 按项目聚合

### Fallback 保护

- **Circuit Breaker 状态** — 实时查看哪些 provider 被熔断
- **Fallback 事件流** — 每次降级的完整记录（从哪个模型、到哪个模型、原因、时间）

## 配置

`~/.msc/config.json`：

```json
{
  "default_level": 2,
  "levels": {
    "1": {"name": "glm", "provider": "glm", "model": "glm-5.1", "context": "闲聊、Q&A、文件查找"},
    "2": {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-4-6", "context": "编码、测试、调试"},
    "3": {"name": "opus", "provider": "anthropic", "model": "claude-opus-4-6", "context": "架构设计、深度审计"}
  },
  "providers": {
    "glm": {"url": "https://open.bigmodel.cn/api/anthropic/v1/messages", "key": "..."},
    "anthropic": {"url": "https://api.anthropic.com", "passthrough_auth": true}
  },
  "fallback": {
    "error_rules": {
      "_default": {
        "retriable_http": [429, 500, 502, 503, 529],
        "fatal_http": [400, 401, 403, 404]
      },
      "glm": {
        "business_code_path": "error.code",
        "retriable_codes": ["1200", "1230", "1234", "1302", "1303", "1305", "1312"],
        "fatal_codes": ["1301", "1304", "1308", "1309", "1310", "1311", "1313",
                        "1000", "1001", "1002", "1003", "1004",
                        "1110", "1111", "1112", "1113", "1121"]
      }
    }
  }
}
```

关键配置项：

| 字段 | 说明 |
|------|------|
| `levels.N.context` | 路由场景描述 — AI 根据此描述判断何时切换到该模型 |
| `providers` | 模型厂商配置 — 支持任何兼容 Anthropic API 格式的接口 |
| `fallback.error_rules` | 错误分类规则 — `_default` 为通用规则，按 provider 名覆盖 |
| `fallback.error_rules.*.retriable_codes` | 触发 fallback 的业务错误码 |
| `fallback.error_rules.*.fatal_codes` | 不触发 fallback、直接返回的错误码 |

## 架构

```
model-steer-claude/
├── .claude-plugin/plugin.json   # 插件清单
├── hooks/
│   ├── hooks.json               # 自注册 hooks
│   ├── session-start.sh         # 向代理注册 session
│   └── user-prompt-submit.sh    # 显示当前等级
├── skills/                      # /smoke、/redbull、/think-level
├── commands/                    # 斜杠命令定义
├── proxy.py                     # 核心代理（fallback + 错误分类 + 熔断）
├── config/default-config.json   # 默认配置
├── ui/dashboard.html            # Dashboard 单页应用
└── install.sh                   # 一键安装
```

核心设计：

- **插件隔离** — NB-Claude 只在 `cr` 启动时加载，普通 `claude` 不受影响
- **零重试 fallback** — 纯 fail-fast 降级，避免与 Claude Code 内置重试叠加（3 模型 × 10 重试 = 90 次请求）
- **错误码优先** — per-provider 业务错误码覆盖 HTTP 状态码，GLM 1234（HTTP 400）也能正确触发 fallback
- **规则驱动路由** — 路由决策由你定义的场景描述驱动，不是硬编码，改配置即时生效
- **签名修补** — 跨模型会话时自动修补 thinking-block 签名，无缝切换

## Contact

- Email: libeny0526@gmail.com
- WeChat: BiothaLMY

## License

MIT
