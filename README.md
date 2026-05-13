<div align="center">

# Weather Agents

**雾 · 雨 · 霜 · 雪 · 露**

*五位气象 Agent 各司其职，通过技能系统与事件总线协作，完成任何复杂任务。*

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://python.org)
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
│                  Web Dashboard (FastAPI + WebSocket)         │
├───────────┬───────────┬───────────┬───────────┬──────────────┤
│  🌫️ Fog   │  🌧️ Rain  │  ❄️ Frost  │  🌨️ Snow  │   💧 Dew     │
│  探索研究  │  生成创造  │  审查优化  │  规划编排  │   运维集成   │
├───────────┴───────────┴───────────┴───────────┴──────────────┤
│                    Skill System (15 composable skills)       │
├──────────────────────────────────────────────────────────────┤
│          Tool Registry · 9 Built-in Tools · MCP Protocol     │
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
| 🌫️ **Fog** | 雾 | 探索研究 | `web_research` · `code_analysis` · `doc_analysis` |
| 🌧️ **Rain** | 雨 | 生成创造 | `code_generator` · `content_writer` · `data_transformer` |
| ❄️ **Frost** | 霜 | 审查优化 | `code_reviewer` · `security_audit` · `performance_check` |
| 🌨️ **Snow** | 雪 | 规划编排 | `task_planner` · `arch_designer` · `workflow_designer` |
| 💧 **Dew** | 露 | 运维集成 | `sys_operator` · `ci_cd` · `api_integrator` |

## Quick Start

### 1. Install

```bash
git clone https://github.com/susurrune/weather-agents.git
cd weather-agents
pip install -e .
```

### 2. Configure

```bash
# 方式一：环境变量（推荐）
cp .env.example .env
# 编辑 .env 填入 API Key

# 方式二：CLI 配置
wa config set api_key.openai sk-xxx
wa config set api_key.anthropic sk-ant-xxx
wa config set api_key.deepseek sk-xxx
```

### 3. Use

```bash
# 交互式对话（默认 Fog Agent）
wa chat

# 指定 Agent 单轮对话
wa chat rain "用 Python 写一个 LRU Cache"

# 多 Agent 协作编排
wa task "设计并实现一个 URL 短链接服务"

# 启动 Web 仪表盘
wa web
```

## CLI Reference

### Top-level Commands

| Command | Description |
|:--------|:------------|
| `wa chat [agent] [message]` | 对话（默认 `fog`，支持 `fog` `rain` `frost` `snow` `dew`）|
| `wa task <goal>` | Snow Agent 拆解目标并调度多 Agent 协作 |
| `wa status` | 查看所有 Agent 状态 |
| `wa web` | 启动 Web Dashboard (`http://127.0.0.1:8765`) |
| `wa config list\|set\|delete` | 查看/修改/删除配置 |
| `wa memory status\|clear` | 查看/清除记忆 |

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
| `/cost` | 查看 Token 用量和费用 |
| `/history` | 查看事件日志 |
| `/mcp` | MCP 服务器状态 |
| `/version` | 版本信息 |
| `/clear` | 清屏 |
| `/quit` | 退出 |

## Features

### Multi-Provider LLM

通过 [LiteLLM](https://github.com/BerriAI/litellm) 接入多家模型，每个 Agent 可独立配置：

| Provider | Models |
|:---------|:-------|
| OpenAI | `gpt-4o` · `gpt-4o-mini` · `gpt-4.1-nano` |
| Anthropic | `claude-sonnet-4-20250514` · `claude-haiku-4-20250414` |
| DeepSeek | `deepseek-chat` · `deepseek-reasoner` |
| Ollama | `ollama/llama3` · `ollama/deepseek-r1` (本地) |

### Skill System

15 个可组合技能，运行时动态激活/关闭，为 Agent 注入专业能力：

```bash
wa chat frost
> /skills              # 查看 Frost 的 3 个技能
> /use security_audit   # 激活安全审计模式
> 审查这段代码的安全性    # Frost 现在拥有安全审计的增强提示词和专属工具
> /deactivate           # 回到基础模式
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

### Web Dashboard

```bash
wa web  # http://127.0.0.1:8765
```

- WebSocket 实时流式响应
- 多 Agent 对话 & 技能切换
- 任务编排可视化
- Session 隔离，支持多用户
- 可选 Bearer Token 认证 (`WA_API_TOKEN`)

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

1. **环境变量** — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `WA_API_TOKEN`
2. **用户配置** — `~/.weather-agents/config.yaml`
3. **项目配置** — `./config/default.yaml`

```yaml
llm:
  default_model: "gpt-4o-mini"
  temperature: 0.7
  max_tokens: 4096
  timeout: 60
  api_keys:
    openai: "${OPENAI_API_KEY}"
    anthropic: "${ANTHROPIC_API_KEY}"
    deepseek: "${DEEPSEEK_API_KEY}"

agents:
  fog:
    model: "gpt-4o"       # 覆盖默认模型
  frost:
    model: "claude-sonnet-4-20250514"

memory:
  db_path: "~/.weather-agents/memory.db"
  short_term_limit: 50
```

## Development

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 测试（94 tests）
pytest tests/ -v

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
| Runtime | Python 3.12+ · asyncio |
| LLM | LiteLLM (OpenAI / Anthropic / DeepSeek / Ollama) |
| Web | FastAPI · WebSocket · Uvicorn |
| CLI | Typer · Rich |
| Memory | aiosqlite · 3-layer architecture |
| Search | DuckDuckGo (built-in, no API key) |
| Tools | MCP Protocol · Plugin system |
| CI | GitHub Actions · Ruff · MyPy · Pytest |

## License

[MIT](LICENSE)
