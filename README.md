<div align="center">

# Weather Agents

**雾 · 雨 · 霜 · 雪 · 露**

*五位气象 Agent 各司其职，通过技能系统与事件总线协作，完成任何复杂任务。*

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/susurrune/weather-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/susurrune/weather-agents/actions)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/susurrune/weather-agents)

</div>

---

## Why Weather Agents?

大多数 AI 工具都是单一模型 + 单一提示词。Weather Agents 不同——它将任务分解给**专精不同领域的 Agent**，像一支配合默契的团队：规划者拆解目标，研究者搜集信息，工程师编写代码，审计师把关质量，运维者落地执行。

```
用户: "帮我搭建一个 FastAPI 项目"

  🌨️ Snow  → 拆解为 5 个子任务，分配给合适的 Agent
  🌫️ Fog   → 调研最佳实践和项目结构
  🌧️ Rain  → 生成项目代码和配置文件
  ❄️ Frost → 审查代码质量和安全性
  💧 Dew   → 初始化 Git、安装依赖、验证运行
```

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                  CLI (Typer + Rich)                          │
├───────────┬───────────┬───────────┬───────────┬──────────────┤
│  🌫️ Fog   │  🌧️ Rain  │  ❄️ Frost  │  🌨️ Snow  │   💧 Dew     │
│  探索研究  │  生成创造  │  审查优化  │  规划编排  │   运维集成   │
├───────────┴───────────┴───────────┴───────────┴──────────────┤
│                    Skill System (15 composable skills)       │
├──────────────────────────────────────────────────────────────┤
│        Tool Registry · 15 Built-in Tools · MCP Protocol      │
├──────────────────────────────────────────────────────────────┤
│              Event Bus (pub/sub · orchestration)              │
├────────────────────────────┬─────────────────────────────────┤
│  LLM (LiteLLM multi-provider) │  Memory (SQLite · 3-layer)  │
│  Config (YAML + ENV)          │  Plugins · Cache · Budget    │
└────────────────────────────┴─────────────────────────────────┘
```

## Agents

| Agent | 中文 | 职能 | 专属技能 |
|:------|:-----|:-----|:---------|
| 🌫️ **Fog** | 雾 | 探索研究 | `web_research` · `code_analysis` · `document_analysis` |
| 🌧️ **Rain** | 雨 | 生成创造 | `code_generator` · `content_writer` · `data_transformer` |
| ❄️ **Frost** | 霜 | 审查优化 | `code_reviewer` · `security_auditor` · `performance_checker` |
| 🌨️ **Snow** | 雪 | 规划编排 | `task_planner` · `arch_designer` · `workflow_designer` |
| 💧 **Dew** | 露 | 运维集成 | `sys_operator` · `ci_cd_manager` · `api_integrator` |

## Quick Start

### 1. Install

```bash
pip install git+https://github.com/susurrune/weather-agents.git
```

### 2. Configure

首次运行 `wa chat` 时会自动进入设置向导，让你选择：

- **Unified（推荐）**：所有 5 个 Agent 共用一个模型 + 一个 API key
- **Per-agent**：为每个 Agent 单独挑选模型（适合混搭，比如 Snow 用 Claude 做规划，Rain 用 GPT 写代码，其它用 DeepSeek）

向导只会向你实际选中的 provider 索要 API key。也可以显式重新配置：

```bash
wa init                                    # 重新跑向导
wa config set api_key.deepseek sk-xxx       # 直接写单条
export DEEPSEEK_API_KEY=sk-xxx              # 或用环境变量
```

### 3. Use

```bash
# 交互式对话（默认 Fog Agent）
wa chat

# 指定 Agent 单轮对话
wa chat rain "用 Python 写一个 LRU Cache"

# 多 Agent 协作编排
wa task "设计并实现一个 URL 短链接服务"
```

## CLI Reference

### Top-level Commands

| Command | Description |
|:--------|:------------|
| `wa init` | 交互式配置向导（首次运行推荐） |
| `wa chat [agent] [message]` | 对话（默认 `fog`，支持 `fog` `rain` `frost` `snow` `dew`）|
| `wa task <goal>` | Snow Agent 拆解目标并调度多 Agent 协作 |
| `wa status` | 查看所有 Agent 状态 |
| `wa config list\|set\|delete\|models` | 查看/修改/删除配置 · 列出可用模型 |
| `wa memory status\|clear` | 查看/清除记忆 |
| `wa --version` / `wa version` | 版本信息 |

### Interactive Commands

进入 `wa chat` 后可使用：

| Command | Description |
|:--------|:------------|
| `/fog` `/rain` `/frost` `/snow` `/dew` | 切换 Agent |
| `/task <目标>` | 多 Agent 任务编排 |
| `/skills` | 查看当前 Agent 可用技能 |
| `/use <skill>` | 激活技能（增强提示词 + 扩展工具） |
| `/deactivate` | 关闭所有技能 |
| `/status` | Agent 状态一览 |
| `/model [name]` / `/model <agent> <name>` | 查看/切换模型（默认或按 Agent） |
| `/apikey` | 管理 API keys |
| `/cost` · `/cost reset` | 查看 Token 用量和费用 · 重置计数器 |
| `/memory` · `/memory clear` | 查看记忆状态 · 清除所有短期记忆 |
| `/history` | 查看事件日志 |
| `/mcp` | MCP 服务器状态（含已连接工具数） |
| `/version` | 版本信息 |
| `/clear` | 清屏 |
| `/quit` | 退出 |

## Features

### Multi-Provider LLM

通过 [LiteLLM](https://github.com/BerriAI/litellm) 接入多家模型，每个 Agent 可独立配置：

| Provider | Models |
|:---------|:-------|
| OpenAI | `gpt-4o` · `gpt-4o-mini` · `gpt-4.1` · `gpt-4.1-mini` · `gpt-4.1-nano` |
| Anthropic | `claude-opus-4-7` · `claude-sonnet-4-6` · `claude-haiku-4-5` |
| DeepSeek | `deepseek-v4-flash` · `deepseek-v4-pro` |
| Ollama | `ollama/llama3` · `ollama/qwen2.5` · `ollama/deepseek-r1` (本地) |

> Use `wa config models` to see what your installation currently supports.

### Skill System

15 个可组合技能，运行时动态激活/关闭，为 Agent 注入专业能力：

```bash
wa chat frost
> /skills                # 查看 Frost 的 3 个技能
> /use security_auditor  # 激活安全审计模式
> 审查这段代码的安全性     # Frost 现在拥有安全审计的增强提示词和专属工具
> /deactivate            # 回到基础模式
```

### Three-Layer Memory

| Layer | Scope | Storage | Purpose |
|:------|:------|:--------|:--------|
| **Short-term** | 会话级 | SQLite | 对话上下文，自动截断 |
| **Working** | 任务级 | In-memory | 任务执行中的临时状态 |
| **Long-term** | 持久 | SQLite KV | 带分类的知识记忆，支持模糊搜索 |

### Task Orchestration

Snow Agent 将复杂目标分解为带依赖关系的子任务，并行调度执行：

```
Goal: "搭建微服务项目"
  ├─ [1] Fog: 调研微服务最佳实践
  ├─ [2] Rain: 生成项目骨架 (depends: 1)
  ├─ [3] Rain: 编写 Dockerfile (depends: 2)
  ├─ [4] Frost: 代码审查 (depends: 2)
  └─ [5] Dew: 部署验证 (depends: 3, 4)
```

- 自动依赖排序，无依赖的任务并行执行
- 失败任务自动重试（最多 3 轮）
- 结果汇总报告

### Cost Control

内置费用追踪和预算控制：

```bash
> /cost   # 查看各 Agent 累计 Token 和费用
```

```python
# 代码中设置预算上限
llm = LLMClient(config, cost_limit=5.0)  # 超过 $5 自动停止
```

### Terminal Agent Tools

15 个内置工具，让 Agent 直接操作本地文件和执行命令：

| Tool | Description |
|:-----|:------------|
| `read_file` / `write_file` / `edit_file` | 文件读写编辑 |
| `list_directory` / `tree` | 目录浏览 |
| `move_file` / `copy_file` / `delete_file` | 文件管理 |
| `file_search` / `code_search` | 搜索（`code_search` 支持 `regex=true`）|
| `shell_exec` | 安全执行命令（非 shell，禁用管道/重定向；危险命令黑名单）|
| `http_get` / `http_post` | HTTP 请求（默认拒绝私网/回环/IMDS）|
| `web_search` | DuckDuckGo 搜索 |
| `get_cwd` | 获取工作目录 |

### Safety Defaults

- **`shell_exec`** 使用 `subprocess` 的参数列表模式，不解析 shell 元字符（`;` `|` `&&` `$(...)`）。
  自动拒绝危险二进制（`sudo` `dd` `mkfs` `shutdown` 等）和针对系统根、用户家目录、Windows 盘符根的 `rm -rf`。
- **`http_get` / `http_post`** 默认拒绝 `localhost`、私网 IP（10/172.16/192.168/...）、回环、链路本地、IMDS 端点。
  需要访问内网时设置 `WA_ALLOW_PRIVATE_NET=1` 显式放行。
- 所有长输出（文件、stdout/stderr、HTTP body、搜索结果）以可见的截断标记结尾，避免 LLM 误以为是完整内容。

### MCP Integration

支持 [Model Context Protocol](https://modelcontextprotocol.io) 扩展工具集：

```yaml
# ~/.weather-agents/config.yaml
mcp:
  servers:
    - name: "filesystem"
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/home"]
      transport: "stdio"
      enabled: true
```

### Plugin System

将自定义工具放入 `~/.weather-agents/plugins/` 即可自动加载：

```python
from weather_agents.plugins.loader import Plugin
from weather_agents.core.tool import Tool, ToolParameter

def create_plugin() -> Plugin:
    plugin = Plugin("my-plugin")
    plugin.register_tool(Tool(
        name="my_tool",
        description="My custom tool",
        parameters=[ToolParameter(name="input", type="string", description="Input")],
        handler=lambda input: f"processed: {input}",
    ))
    return plugin
```

## Configuration

配置按优先级从高到低合并：

1. **环境变量** — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`
2. **用户配置** — `~/.weather-agents/config.yaml`
3. **项目配置** — `./config/default.yaml`

```yaml
llm:
  default_model: "deepseek/deepseek-v4-flash"
  temperature: 0.7
  max_tokens: 4096
  timeout: 60
  api_keys:
    openai: "${OPENAI_API_KEY}"
    anthropic: "${ANTHROPIC_API_KEY}"
    deepseek: "${DEEPSEEK_API_KEY}"

agents:
  fog:
    model: "gpt-4o"               # 覆盖默认模型
  frost:
    model: "claude-sonnet-4-6"

memory:
  db_path: "~/.weather-agents/memory.db"
  short_term_limit: 50
```

## Development

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 测试（107 tests, 60%+ 覆盖率）
pytest tests/ -v --cov=weather_agents

# Lint
ruff check src/ tests/

# Type check
mypy src/

# Format
ruff format src/ tests/
```

## Tech Stack

| Component | Technology |
|:----------|:-----------|
| Runtime | Python 3.11+ · asyncio |
| LLM | LiteLLM (OpenAI / Anthropic / DeepSeek / Ollama) |
| CLI | Typer · Rich (spinner, markdown, tables) |
| Memory | aiosqlite · 3-layer architecture |
| Search | DuckDuckGo (built-in, no API key) |
| Tools | 15 built-in · MCP Protocol · Plugin system |
| CI | GitHub Actions · Ruff · MyPy · Pytest |

## License

[MIT](LICENSE)
