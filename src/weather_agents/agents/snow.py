"""雪 (Snow) — 规划编排 Agent."""

from __future__ import annotations

import json
import re

from weather_agents.core.agent import BaseAgent, Task
from weather_agents.core.bus import Event, EventType


class SnowAgent(BaseAgent):
    name = "snow"
    display_name = "雪"
    emoji = "🌨️"
    specialty = "规划编排"
    tool_names = [
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "tree",
        "file_search",
        "code_search",
        "shell_exec",
        "get_cwd",
        "move_file",
        "copy_file",
        "delete_file",
        "web_search",
        "http_get",
        "http_post",
    ]
    skill_names = ["task_planner", "arch_designer", "workflow_designer"]

    system_prompt = """你是 Weather Agents 的「雪」— 覆盖全局，让一切有序运行。

## 身份
- **产品**: Weather Agents 多智能体终端
- **专长**: 任务编排、架构设计、流程管理
- **风格**: 系统化、全局视角、有预见性

## 调度台
| Agent | 专长 | 典型任务 |
|-------|------|---------|
| 🌫️ 雾 | 探索研究 | 信息检索、代码分析、趋势洞察 |
| 🌧️ 雨 | 生成创造 | 代码编写、内容创作、数据转换 |
| ❄️ 霜 | 审查优化 | 代码审查、安全审计、性能检测 |
| 💧 露 | 运维集成 | 命令执行、部署操作、API 集成 |

## 任务分解格式
```json
{
  "goal": "目标",
  "steps": [
    {"id": "1", "description": "...", "agent": "rain", "depends_on": [], "priority": "high"}
  ]
}
```
agent 取值: fog / rain / frost / dew / snow

## 回复规范
1. 任务计划用表格呈现: 序号 | Agent | 任务 | 依赖 | 优先级
2. 标注预计耗时和风险点
3. 汇总执行结果: 成功数/总数 + 关键产出
4. 规划就是规划——先展示结构再解释理由"""

    system_prompt_en = """You are "Snow" of Weather Agents — covering the whole landscape, keeping everything in order.

## Identity
- **Product**: Weather Agents multi-agent terminal
- **Specialty**: Task orchestration, architecture design, workflow management
- **Style**: Systematic, holistic, forward-looking

## Dispatch Board
| Agent | Specialty | Typical Tasks |
|-------|-----------|---------------|
| 🌫️ Fog | Research | Info retrieval, code analysis, trend insights |
| 🌧️ Rain | Creation | Code writing, content creation, data transformation |
| ❄️ Frost | Review | Code review, security audit, performance check |
| 💧 Dew | Operations | Command execution, deployment, API integration |

## Task Decomposition Format
```json
{
  "goal": "goal description",
  "steps": [
    {"id": "1", "description": "...", "agent": "rain", "depends_on": [], "priority": "high"}
  ]
}
```
Valid agent values: fog / rain / frost / dew / snow

## Response Rules
1. Present task plan as a table: # | Agent | Task | Depends On | Priority
2. Include estimated time and risk notes
3. Summarize execution results: success/total + key outputs
4. Planning is planning — show the structure first, then explain"""

    async def orchestrate(self, goal: str) -> list[Task]:
        """Decompose a goal into tasks and dispatch to agents."""
        prompt = (
            f"请将以下目标分解为子任务，并分配给合适的 Agent。\n\n"
            f"目标: {goal}\n\n"
            f"请严格按照 JSON 格式输出任务计划。"
        )

        self.memory.add_message("user", prompt)
        response = await self._llm_loop()
        self.memory.add_message("assistant", response.content)

        tasks = self._parse_task_plan(response.content, goal)

        for task in tasks:
            if task.assigned_to and task.assigned_to != self.name:
                await self.bus.publish(
                    Event(
                        type=EventType.TASK_ASSIGNED,
                        source=self.name,
                        target=task.assigned_to,
                        data={
                            "id": task.id,
                            "description": task.description,
                            "parent_id": task.parent_id,
                            "metadata": task.metadata,
                        },
                    )
                )

        return tasks

    def _parse_task_plan(self, content: str, goal: str) -> list[Task]:
        """Extract task plan from LLM response with robust JSON parsing."""
        # Try to extract JSON from markdown code blocks first
        json_str = self._extract_json(content)
        if json_str:
            try:
                plan = json.loads(json_str)
                tasks = self._plan_to_tasks(plan, goal)
                if tasks is not None:
                    return tasks
            except (json.JSONDecodeError, KeyError):
                pass

        # Try to find raw JSON in response
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                plan = json.loads(content[start:end])
                tasks = self._plan_to_tasks(plan, goal)
                if tasks is not None:
                    return tasks
        except (json.JSONDecodeError, KeyError):
            pass

        # Fallback: single task for rain
        return [Task(id="1", description=goal, assigned_to="rain", metadata={"goal": goal})]

    @staticmethod
    def _extract_json(content: str) -> str | None:
        """Extract JSON from markdown code blocks."""
        patterns = [
            re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL),
            re.compile(r"```\s*\n(\{.*?\})\n```", re.DOTALL),
        ]
        for pattern in patterns:
            match = pattern.search(content)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _plan_to_tasks(plan: dict, goal: str) -> list[Task]:
        """Convert a parsed plan dict to Task objects."""
        valid_agents = {"fog", "rain", "frost", "snow", "dew"}
        tasks: list[Task] = []
        for step in plan.get("steps", []):
            agent = step.get("agent", "rain")
            if agent not in valid_agents:
                agent = "rain"
            depends = step.get("depends_on", [])
            parent_id = depends[0] if depends else None
            tasks.append(
                Task(
                    id=str(step.get("id", len(tasks) + 1)),
                    description=step.get("description", ""),
                    assigned_to=agent,
                    parent_id=parent_id,
                    metadata={
                        "goal": goal,
                        "priority": step.get("priority", "medium"),
                    },
                )
            )
        return tasks
