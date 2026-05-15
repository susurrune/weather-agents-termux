"""delegate_to — allows an agent to hand off work to a specialist agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from weather_agents.core.bus import Event, EventType
from weather_agents.core.logger import get_logger
from weather_agents.core.tool import Tool, ToolParameter

if TYPE_CHECKING:
    from weather_agents.core.agent import BaseAgent

_log = get_logger("delegate")

AGENT_SPECIALTIES: dict[str, str] = {
    "fog": "research / code analysis / knowledge retrieval",
    "rain": "code generation / content creation / data transformation",
    "frost": "code review / security audit / performance analysis",
    "snow": "task planning / architecture design / workflow management",
    "dew": "command execution / deployment / API integration",
}

_MAX_RESULT_CHARS = 8000


def create_delegate_tool(agent_map: dict[str, BaseAgent]) -> Tool:
    """Build a ``delegate_to`` tool whose handler closes over *agent_map*.

    Call this **after** all agents have been constructed so the handler
    can look up target agents at execution time.
    """
    from weather_agents.core.agent import AgentState, Task

    _delegation_depth = 0

    async def _handle(agent: str, task: str, context: str = "") -> str:
        nonlocal _delegation_depth

        if agent not in agent_map:
            names = ", ".join(sorted(agent_map.keys()))
            return f"Unknown agent '{agent}'. Available agents: {names}"

        target = agent_map[agent]

        if _delegation_depth > 0:
            return (
                "Nested delegation is not supported. "
                f"Agent '{agent}' must complete the task directly using its own tools."
            )

        _delegation_depth += 1
        try:
            await target.init()

            task_obj = Task(
                id=f"dlg-{id(task) & 0xFFFF:04x}",
                description=task,
                assigned_to=agent,
                metadata={"context": context} if context else {},
            )

            _log.info(
                "delegation_start",
                extra={"target": agent, "task": task[:120]},
            )

            target.bus.add_event(
                Event(
                    type=EventType.TOOL_CALL,
                    source=agent,
                    data={"tool": "delegate_to", "phase": "start", "task": task[:200]},
                )
            )

            result = await target.execute_task(task_obj)

            if target.state == AgentState.ERROR:
                await target._set_state(AgentState.IDLE)

            target.bus.add_event(
                Event(
                    type=EventType.TOOL_CALL,
                    source=agent,
                    data={
                        "tool": "delegate_to",
                        "phase": "done",
                        "success": result.success,
                    },
                )
            )

            content = result.content
            if len(content) > _MAX_RESULT_CHARS:
                content = content[:_MAX_RESULT_CHARS] + "\n\n… (truncated)"

            status = "completed" if result.success else "failed"
            header = f"[{target.emoji} {target.display_name}] {status}"

            _log.info(
                "delegation_done",
                extra={
                    "target": agent,
                    "success": result.success,
                    "chars": len(result.content),
                },
            )

            return f"{header}\n\n{content}"

        except Exception as exc:
            _log.exception("delegation_error: %s", exc)
            return f"Delegation to '{agent}' failed: {exc}"
        finally:
            _delegation_depth -= 1

    return Tool(
        name="delegate_to",
        description=(
            "Delegate a task to a specialist agent and receive the result. "
            "Use this when a task would benefit from another agent's expertise. "
            "Available agents and their specialties:\n"
            + "\n".join(f"  - {k}: {v}" for k, v in AGENT_SPECIALTIES.items())
        ),
        parameters=[
            ToolParameter(
                name="agent",
                type="string",
                description=("Target agent name. One of: fog, rain, frost, snow, dew."),
                required=True,
            ),
            ToolParameter(
                name="task",
                type="string",
                description="Clear, specific description of what the agent should do.",
                required=True,
            ),
            ToolParameter(
                name="context",
                type="string",
                description="Additional context or data the target agent needs.",
                required=False,
                default="",
            ),
        ],
        handler=_handle,
    )
