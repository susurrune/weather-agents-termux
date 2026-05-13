"""Base agent class for all Weather Agents."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from weather_agents.core.bus import Event, EventType, MessageBus
from weather_agents.core.config import AppConfig
from weather_agents.core.llm import LLMClient, LLMResponse
from weather_agents.core.memory import Memory
from weather_agents.core.skill import Skill, SkillRegistry
from weather_agents.core.tool import Tool, ToolRegistry


class AgentState(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    WAITING = "waiting"
    ERROR = "error"


@dataclass
class Task:
    id: str
    description: str
    assigned_to: str | None = None
    parent_id: str | None = None
    status: str = "pending"
    result: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class TaskResult:
    success: bool
    content: str
    data: dict = field(default_factory=dict)


class BaseAgent:
    """Base class for all Weather Agents."""

    name: str = ""
    display_name: str = ""
    emoji: str = ""
    specialty: str = ""
    system_prompt: str = ""
    tool_names: list[str] = []
    skill_names: list[str] = []

    def __init__(
        self,
        config: AppConfig,
        llm: LLMClient,
        bus: MessageBus,
        tool_registry: ToolRegistry,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.bus = bus
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry or SkillRegistry()
        self.state = AgentState.IDLE
        self.memory = Memory(config.memory, self.name)
        self._tools: list[Tool] = []
        self._skills: list[Skill] = []
        self._active_skills: set[str] = set()
        self._base_system_prompt: str = ""

    async def init(self) -> None:
        """Initialize agent (memory, subscriptions, skills, etc)."""
        await self.memory.init_db()
        self._base_system_prompt = self.system_prompt
        self.memory.add_message("system", self.system_prompt)
        self._tools = (
            self.tool_registry.get_tools(self.tool_names) or self.tool_registry.get_tools()
        )
        self._load_skills()
        self.bus.subscribe(self.name, self._handle_event)

    def _load_skills(self) -> None:
        """Load pre-installed skills and merge their tool requirements."""
        self._skills = self.skill_registry.get_skills(self.skill_names) if self.skill_names else []
        for skill in self._skills:
            for tool_name in skill.required_tools:
                tool = self.tool_registry.get(tool_name)
                if tool and tool not in self._tools:
                    self._tools.append(tool)

    def activate_skill(self, name: str) -> bool:
        """Activate a skill by name. Returns True if found and activated."""
        if not any(s.name == name for s in self._skills):
            return False
        self._active_skills.add(name)
        self._rebuild_system_prompt()
        return True

    def deactivate_skill(self, name: str) -> bool:
        """Deactivate a skill. Returns True if it was active."""
        if name not in self._active_skills:
            return False
        self._active_skills.discard(name)
        self._rebuild_system_prompt()
        return True

    def deactivate_all_skills(self) -> None:
        """Deactivate all skills and restore the base system prompt."""
        self._active_skills.clear()
        self._rebuild_system_prompt()

    def _rebuild_system_prompt(self) -> None:
        """Rebuild the system prompt with active skill prompts appended."""
        if not self._active_skills:
            prompt = self._base_system_prompt
        else:
            skill_prompts = [
                skill.system_prompt
                for skill in self._skills
                if skill.name in self._active_skills and skill.system_prompt
            ]
            prompt = self._base_system_prompt
            if skill_prompts:
                prompt += "\n\n" + "\n\n".join(skill_prompts)

        for _i, msg in enumerate(self.memory.short_term):
            if msg.role == "system":
                msg.content = prompt
                break
        else:
            self.memory.add_message("system", prompt)

    def get_active_skills(self) -> list[str]:
        return list(self._active_skills)

    def get_available_skills(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "active": s.name in self._active_skills,
            }
            for s in self._skills
        ]

    async def close(self) -> None:
        await self.memory.close()
        self.bus.unsubscribe(self.name)

    async def _set_state(self, new_state: AgentState) -> None:
        if self.state != new_state:
            old_state = self.state
            self.state = new_state
            event = Event(
                type=EventType.STATE_CHANGE,
                source=self.name,
                data={"old_state": old_state.value, "new_state": new_state.value},
            )
            self.bus.add_event(event)
            await self.bus.notify_state_change(event)

    async def _handle_event(self, event: Event) -> None:
        if event.type == EventType.TASK_ASSIGNED and event.target == self.name:
            task = Task(**event.data)
            result = await self.execute_task(task)
            await self.bus.publish(
                Event(
                    type=EventType.TASK_COMPLETED,
                    source=self.name,
                    target=event.source,
                    data={
                        "task_id": task.id,
                        "success": result.success,
                        "content": result.content,
                    },
                )
            )

    async def chat(
        self,
        message: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """General-purpose chat mode with optional status callback.

        Args:
            message: User message.
            on_status: Called with a status string when state changes
                       (e.g. "thinking...", "calling read_file...").
        """
        await self._set_state(AgentState.THINKING)
        self.memory.add_message("user", message)

        try:
            if on_status:
                on_status("thinking...")
            response = await self._llm_loop(on_status=on_status)
            self.memory.add_message("assistant", response.content)
            await self._set_state(AgentState.IDLE)
            return response.content
        except Exception as e:
            await self._set_state(AgentState.ERROR)
            error_msg = f"[{self.display_name}] Error: {e}"
            self.memory.add_message("assistant", error_msg)
            return error_msg

    async def chat_stream(self, message: str) -> AsyncIterator[str]:
        """Streaming chat mode. Yields content chunks as they arrive."""
        await self._set_state(AgentState.THINKING)
        self.memory.add_message("user", message)

        full_content = ""
        messages = self.memory.get_messages()

        try:
            async for chunk in self.llm.stream(messages=messages, agent_name=self.name):
                full_content += chunk
                yield chunk

            self.memory.add_message("assistant", full_content)
            await self._set_state(AgentState.IDLE)
        except Exception as e:
            await self._set_state(AgentState.ERROR)
            yield f"\n[Error: {e}]"

    async def _llm_loop(
        self,
        max_iterations: int = 10,
        on_status: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """LLM reasoning loop with tool calling support."""
        response = LLMResponse(content="")

        for _ in range(max_iterations):
            messages = self.memory.get_messages()
            if on_status:
                on_status("thinking...")
            response = await self.llm.complete(
                messages=messages,
                agent_name=self.name,
                tools=self.tool_names or None,
            )

            if not response.tool_calls:
                return response

            self.bus.add_event(
                Event(
                    type=EventType.LLM_CALL,
                    source=self.name,
                    data={"model": response.model, "usage": response.usage},
                )
            )

            # Record assistant message with tool_calls
            self.memory.add_message(
                "assistant",
                response.content or "",
                tool_calls=response.tool_calls,
            )

            for tc in response.tool_calls:
                tool = self.tool_registry.get(tc["name"])
                tool_label = _tool_status_label(tc["name"], tc["arguments"])

                self.bus.add_event(
                    Event(
                        type=EventType.TOOL_CALL,
                        source=self.name,
                        data={"tool": tc["name"], "args": tc["arguments"]},
                    )
                )

                if on_status:
                    on_status(tool_label)

                if tool:
                    await self._set_state(AgentState.ACTING)
                    result = await tool.execute(**tc["arguments"])
                    self.memory.add_message(
                        "tool",
                        result,
                        name=tc["name"],
                        tool_call_id=tc["id"],
                    )
                else:
                    self.memory.add_message(
                        "tool",
                        f"Tool '{tc['name']}' not found",
                        name=tc["name"],
                        tool_call_id=tc["id"],
                    )

            await self._set_state(AgentState.THINKING)

        return response

    async def execute_task(
        self,
        task: Task,
        on_status: Callable[[str], None] | None = None,
    ) -> TaskResult:
        """Execute a specific task using agent specialty."""
        await self._set_state(AgentState.THINKING)
        task.status = "in_progress"
        self.memory.set_working("current_task", task)

        prompt = f"Please complete this task: {task.description}"
        if task.metadata:
            ctx_data = {k: v for k, v in task.metadata.items() if k != "goal"}
            if ctx_data:
                prompt += f"\nContext: {json.dumps(ctx_data, ensure_ascii=False)}"

        self.memory.add_message("user", prompt)

        try:
            response = await self._llm_loop(on_status=on_status)
            self.memory.add_message("assistant", response.content)
            task.status = "completed"
            task.result = response.content
            await self._set_state(AgentState.IDLE)
            return TaskResult(success=True, content=response.content)
        except Exception as e:
            task.status = "failed"
            task.result = str(e)
            await self._set_state(AgentState.ERROR)
            return TaskResult(success=False, content=str(e))

    async def request_help(self, target_agent: str, description: str) -> None:
        """Request another agent's assistance."""
        await self.bus.publish(
            Event(
                type=EventType.AGENT_REQUEST,
                source=self.name,
                target=target_agent,
                data={"description": description},
            )
        )

    def get_status(self) -> dict:
        usage = self.llm.get_usage_stats().get(self.name, {})
        return {
            "name": self.name,
            "display_name": self.display_name,
            "emoji": self.emoji,
            "specialty": self.specialty,
            "state": self.state.value,
            "skills": self.get_available_skills(),
            "usage": {
                "calls": usage.get("calls", 0),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "cost": round(usage.get("cost", 0.0), 6),
            },
        }


# -- Helpers ---------------------------------------------------------------

_TOOL_LABELS: dict[str, str] = {
    "read_file": "Reading {path}",
    "write_file": "Writing {path}",
    "edit_file": "Editing {path}",
    "list_directory": "Listing {path}",
    "file_search": "Searching {directory}/{pattern}",
    "code_search": "Searching for '{query}'",
    "shell_exec": "Running: {command}",
    "http_get": "GET {url}",
    "http_post": "POST {url}",
    "web_search": "Searching: {query}",
    "move_file": "Moving {src}",
    "copy_file": "Copying {src}",
    "delete_file": "Deleting {path}",
    "get_cwd": "Getting working directory",
    "tree": "Tree {directory}",
}


def _tool_status_label(name: str, args: dict) -> str:
    """Build a human-readable one-liner for a tool call."""
    template = _TOOL_LABELS.get(name)
    if template:
        try:
            label = template.format_map(args)
        except (KeyError, IndexError):
            label = f"{name}..."
    else:
        label = f"{name}..."
    # Truncate long labels
    if len(label) > 60:
        label = label[:57] + "..."
    return label
