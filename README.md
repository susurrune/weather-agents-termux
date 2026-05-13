# Weather Agents 🌤️ — 多智能体协作系统

雾·雨·霜·雪·露 —— 五种 Agent 各司其职，通过技能系统和事件总线协作完成复杂任务。

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                      CLI (typer + rich)                  │
│                   Web Dashboard (FastAPI)                │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│  🌫️ Fog  │  🌧️ Rain │  ❄️ Frost │  🌨️ Snow  │   💧 Dew    │
│ 探索研究  │ 生成创造  │ 审查优化  │ 规划编排  │  运维集成    │
├──────────┴──────────┴──────────┴──────────┴─────────────┤
│                   Skills System (15 skills)              │
├─────────────────────────────────────────────────────────┤
│        Tool Registry + 9 Built-in Tools + MCP            │
├─────────────────────────────────────────────────────────┤
│           Message Bus (events + orchestration)            │
├─────────────────────────────────────────────────────────┤
│      LLM Layer (LiteLLM) │ Memory (SQLite + working)     │
│      Config (YAML + env) │ Plugins │ Cache               │
└─────────────────────────────────────────────────────────┘
```

## Agents

| Agent | Name | Emoji | 专长 | 技能 |
|-------|------|-------|------|------|
| Fog | 雾 | 🌫️ | 探索研究 | 网络调研, 代码分析, 文档分析 |
| Rain | 雨 | 🌧️ | 生成创造 | 代码生成, 内容写作, 数据转换 |
| Frost | 霜 | ❄️ | 审查优化 | 代码审查, 安全审计, 性能检查 |
| Snow | 雪 | 🌨️ | 规划编排 | 任务规划, 架构设计, 工作流设计 |
| Dew | 露 | 💧 | 运维集成 | 系统操作, CI/CD, API集成 |

## 快速开始

```bash
# 安装
pip install -e .

# 配置 API 密钥
wa config set api_key.openai sk-xxx

# 启动交互式 CLI
wa chat

# 单轮对话
wa chat fog "Python 异步编程的最佳实践"

# 多 Agent 任务编排
wa task "搭建一个 FastAPI 项目结构"
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `wa chat [agent]` | 交互式对话（默认 fog） |
| `wa task <goal>` | 多 Agent 任务编排 |
| `wa status` | 查看所有 Agent 状态 |
| `wa config list` | 查看配置 |
| `wa config set <key> <value>` | 设置配置 |
| `wa web` | 启动 Web 仪表盘 |
| `wa memory status` | 查看记忆状态 |

### 交互模式内命令

| 命令 | 说明 |
|------|------|
| `/fog /rain /frost /snow /dew` | 切换 Agent |
| `/task <目标>` | 多 Agent 任务编排 |
| `/skills` | 查看当前 Agent 技能 |
| `/use <技能名>` | 激活技能 |
| `/deactivate` | 关闭所有技能 |
| `/status` | 查看所有 Agent 状态 |
| `/clear` | 清屏 |
| `/quit` | 退出 |

## Web 仪表盘

```bash
wa web
# 打开 http://127.0.0.1:8765
```

支持 WebSocket 实时流式对话、任务编排和技能管理。

## 配置

配置文件位于 `~/.weather-agents/config.yaml`，支持：

```yaml
llm:
  default_model: "gpt-4o-mini"
  temperature: 0.7
  api_keys:
    openai: "${OPENAI_API_KEY}"  # 从环境变量读取

agents:
  fog:
    model: "gpt-4o"  # 覆盖默认模型
```

## 技能系统

每个 Agent 预装 3 个专属技能，可在对话中动态激活：

```bash
# 查看可用技能
/skills

# 激活技能（增强系统提示词 + 扩展工具集）
/use code_analysis

# 关闭所有技能，恢复基础提示词
/deactivate
```

## 插件开发

插件是 `.py` 文件，放置在 `~/.weather-agents/plugins/` 目录：

```python
from weather_agents.plugins.loader import Plugin
from weather_agents.core.tool import Tool, ToolParameter

def create_plugin() -> Plugin:
    plugin = Plugin("my-plugin")
    plugin.register_tool(Tool(
        name="my_tool",
        description="My custom tool",
        parameters=[ToolParameter(name="input", type="string", description="Input")],
        handler=lambda input: f"hello {input}",
    ))
    return plugin
```

## MCP 集成

支持 MCP (Model Context Protocol) 服务器，配置在 `~/.weather-agents/config.yaml`：

```yaml
mcp:
  servers:
    - name: "filesystem"
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/root"]
      enabled: true
```

## 开发

```bash
git clone https://github.com/susurrune/weather-agents.git
cd weather-agents
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 代码检查
ruff check src/ tests/

# 类型检查
mypy src/
```

## 技术栈

- **Python 3.11+** — async/await
- **LiteLLM** — 多模型 LLM 接入
- **FastAPI + WebSocket** — Web 服务
- **SQLite (aiosqlite)** — 持久化记忆
- **Typer + Rich** — CLI
- **MCP** — Model Context Protocol 工具扩展
