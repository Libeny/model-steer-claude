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

5 小时窗口耗尽、7 天 Sonnet 额度见底、API 返回 429 —— 你只能等，工作流直接中断。

MSC 解决这个问题。它是一个本地代理，架在 Claude Code 和模型 API 之间，提供 **三层保障**：

| 层级 | 能力 | 说明 |
|------|------|------|
| **第一层：Never Fallback** | 自动故障转移 | Sonnet 429？自动切 GLM。GLM 也不行？按配置链路继续降级。错误码智能分类，不该重试的（内容安全）不浪费请求 |
| **第二层：任务路由** | AI 自主切换模型 | 开启 `--route` 后，AI 根据任务复杂度自动选模型 —— 闲聊走便宜模型，编码走 Sonnet，架构设计走 Opus |
| **第三层：Agent 编排** | Sub-agent 模型分配 | 多 Agent 协作时，每个 Agent 可以使用最适合其任务的模型（规划中） |

## 工作原理

```
                          ┌─ Sonnet (Anthropic)  ── 429/500? ──┐
用户 → Claude Code → MSC ─┤                                       ├→ 永不中断
   Proxy (本地代理)        └─ GLM / DeepSeek / Moonshine ... ────┘
```

### 第一层：Never Fallback（默认开启）

MSC 代理所有 API 请求。当主模型不可用时，自动按配置链路降级：

```
Sonnet → GLM-5.1 → 其他可用模型
```

**智能错误分类**（不只看 HTTP 状态码）：

| 场景 | 处理方式 |
|------|---------|
| Anthropic 429（额度耗尽） | 自动 fallback |
| Anthropic 500/503（服务异常） | 自动 fallback |
| GLM 1302/1303/1305（临时限流） | 自动 fallback |
| GLM 1301（内容安全审核） | **不 fallback**（换模型也会触发） |
| GLM 1304/1308（配额耗尽） | **不 fallback**（标记 provider 不可用） |
| GLM 1234（网络错误） | 自动 fallback |

错误分类规则可配置，支持任意模型厂商的业务错误码。详见 `~/.msc/config.json` 中的 `fallback.error_rules`。

### 第二层：任务路由（可选，`cr --route`）

默认 `cr` 只做 fallback 保障。加 `--route` 后，MSC 注入路由规则到 system prompt，AI 会根据任务复杂度自动切换模型：

```
"这个文件在哪？"     → GLM（便宜快速）
"帮我写个单元测试"   → Sonnet（编码强）
"设计微服务架构"     → Opus（深度推理）
```

等级数量、模型、用途完全在 Dashboard 上配置，拖拽排序。

### 额度查看

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
cr --route            # 启动 + AI 自主路由
crd                   # 打开 Dashboard
crq                   # 查看模型额度
```

## CLI 命令

```bash
cr                          # 交互式会话（仅 fallback 保障）
cr --route                  # 交互式会话（fallback + AI 路由）
cr -p "解释这个文件"         # 单次输出
cr --resume <session-id>    # 恢复会话
crd                         # 打开 Dashboard
crq                         # 查看额度状态
```

会话内命令：

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

## 配置

`~/.msc/config.json`：

```json
{
  "default_level": 2,
  "levels": {
    "1": {"name": "glm", "provider": "glm", "model": "glm-5.1", "context": "闲聊、Q&A"},
    "2": {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-4-6", "context": "编码、测试"},
    "3": {"name": "opus", "provider": "anthropic", "model": "claude-opus-4-6", "context": "架构设计"}
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

`fallback.error_rules` 支持任意模型厂商：`_default` 定义通用规则，per-provider 键定义特定业务错误码。添加新厂商时，只需增加对应的 provider 配置段。

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
├── proxy.py                     # 核心代理（fallback + 错误分类）
├── config/default-config.json   # 默认配置
├── ui/dashboard.html            # Dashboard 单页应用
└── install.sh                   # 一键安装
```

核心设计：

- **插件隔离** — MSC 只在 `cr` 启动时加载，普通 `claude` 不受影响
- **零重试 fallback** — MSC 不重试，纯 fail-fast 降级，避免与 Claude Code 内置重试叠加
- **错误码优先** — per-provider 业务错误码覆盖 HTTP 状态码分类，确保 GLM 1234（网络错误，HTTP 400）也能正确触发 fallback
- **签名修补** — GLM 的空 thinking-block 签名用合法占位符替换，跨模型会话无缝

## Contact

- Email: libeny0526@gmail.com
- WeChat: BiothaLMY

## License

MIT
