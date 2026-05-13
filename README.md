# Weather Agents

Multi-agent system themed after weather phenomena: Fog, Rain, Frost, Snow, Dew.

## Agents

| Agent | Specialty |
|-------|-----------|
| 🌫️ Fog (雾) | Research & Analysis |
| 🌧️ Rain (雨) | Generation & Creation |
| ❄️ Frost (霜) | Review & Optimization |
| 🌨️ Snow (雪) | Planning & Orchestration |
| 💧 Dew (露) | Operations & Integration |

## Quick Start

```bash
pip install -e .
wa chat fog "analyze this project"
wa task "build a blog system"
wa web  # start web dashboard
```

## Configuration

Edit `~/.weather-agents/config.yaml` or use CLI:

```bash
wa config model.fog claude-sonnet-4-6
wa config default_model gpt-4o
```
