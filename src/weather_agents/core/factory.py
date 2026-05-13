"""System factory — unified Agent creation and task orchestration.

Avoids duplication between CLI and web entry points.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from weather_agents.agents.fog import FogAgent
from weather_agents.agents.rain import RainAgent
from weather_agents.agents.frost import FrostAgent
from weather_agents.agents.snow import SnowAgent
from weather_agents.agents.dew import DewAgent
from weather_agents.core.agent import BaseAgent, Task as AgentTask
from weather_agents.core.bus import MessageBus
from weather_agents.core.config import AppConfig, load_config
from weather_agents.core.llm import LLMClient
from weather_agents.core.tool import global_registry
from weather_agents.tools.builtin import register_builtin_tools

AGENT_CLASSES = {
    "fog": FogAgent,
    "rain": RainAgent,
    "frost": FrostAgent,
    "snow": SnowAgent,
    "dew": DewAgent,
}

AGENT_EMOJI = {
    "fog": "🌫️",
    "rain": "🌧️",
    "frost": "❄️",
    "snow": "🌨️",
    "dew": "💧",
}


@dataclass
class SystemContext:
    """Wires together all shared services for an agent system instance."""

    config: AppConfig
    bus: MessageBus
    llm: LLMClient
    agent_map: dict[str, BaseAgent]

    async def init_all(self) -> None:
        for agent in self.agent_map.values():
            await agent.init()

    async def close_all(self) -> None:
        for agent in self.agent_map.values():
            await agent.close()


def create_system_context() -> SystemContext:
    """Bootstrap the full system: config, bus, LLM, tools, agents."""
    config = load_config()
    bus = MessageBus()
    register_builtin_tools()
    llm = LLMClient(config, global_registry)
    agents = {
        name: cls(config=config, llm=llm, bus=bus, tool_registry=global_registry)
        for name, cls in AGENT_CLASSES.items()
    }
    return SystemContext(config=config, bus=bus, llm=llm, agent_map=agents)


@dataclass
class TaskExecutionResult:
    """Result of executing a single sub-task in an orchestration."""

    id: str
    agent: str
    description: str
    success: bool
    content: str


async def orchestrate_task(
    goal: str,
    agent_map: dict[str, BaseAgent],
    snow: BaseAgent | None = None,
) -> tuple[list[Any], list[TaskExecutionResult], str]:
    """Orchestrate a multi-agent task: plan → execute → summarize.

    Returns (tasks, results, summary).
    """
    if snow is None:
        snow = agent_map.get("snow")
    if snow is None:
        return [], [], "Snow agent not available"

    tasks = await snow.orchestrate(goal)

    results: list[TaskExecutionResult] = []
    for task in tasks:
        assigned = task.assigned_to
        if not assigned or assigned == "snow":
            continue
        agent = agent_map.get(assigned)
        if not agent:
            continue
        a_task = AgentTask(
            id=task.id,
            description=task.description,
            assigned_to=assigned,
            metadata=task.metadata,
        )
        result = await agent.execute_task(a_task)
        results.append(
            TaskExecutionResult(
                id=task.id,
                agent=assigned,
                description=task.description,
                success=result.success,
                content=result.content[:500],
            )
        )

    # Generate summary
    if results:
        summary_prompt = "请汇总以下所有子任务的执行结果：\n\n"
        for r in results:
            status = "成功" if r.success else "失败"
            summary_prompt += f"## 任务 {r['id'] if isinstance(r, dict) else r.id} ({r.agent}) - {status}\n"
            summary_prompt += f"{r.content[:300]}\n\n"
        summary = await snow.chat(summary_prompt)
    else:
        summary = "没有需要执行的任务。"

    return tasks, results, summary
