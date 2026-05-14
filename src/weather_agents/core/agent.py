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
        self._skill_tools: dict[str, list[str]] = {}  # skill_name -> tool_names
        self._base_system_prompt: str = ""
        agent_cfg = getattr(config.agents, self.name, None)
        self._max_tool_rounds: int = agent_cfg.max_tool_rounds if agent_cfg else 10

    def _resolve_system_prompt(self) -> str:
        """Pick the language-appropriate system prompt based on config."""
        lang = getattr(self.config.llm, "language", "zh")
        if lang == "en" and hasattr(self.__class__, "system_prompt_en"):
            return self.__class__.system_prompt_en  # type: ignore[no-any-return]
        return self.system_prompt

    def _inject_workspace_info(self, prompt: str) -> str:
        """Append workspace path to the system prompt."""
        from weather_agents.core.workspace import init_workspace, resolve_workspace_path

        ws_root = resolve_workspace_path(self.config.workspace.path)
        init_workspace(ws_root)  # idempotent — ensures tree exists
        ws_str = str(ws_root.resolve())

        lang = getattr(self.config.llm, "language", "zh")
        if lang == "en":
            ws_block = (
                f"\n\n## Workspace\n"
                f"Your workspace directory is `{ws_str}`.\n"
                f"- Use `{ws_str}/files/` for generated files.\n"
                f"- Use `{ws_str}/output/` for task results and exports.\n"
                f"- Use `{ws_str}/temp/` for temporary/scratch files.\n"
                f"- Always prefer paths under the workspace for file operations."
            )
        else:
            ws_block = (
                f"\n\n## 工作空间\n"
                f"你的工作空间目录是 `{ws_str}`。\n"
                f"- 生成的文件放在 `{ws_str}/files/`\n"
                f"- 任务结果和导出放在 `{ws_str}/output/`\n"
                f"- 临时文件放在 `{ws_str}/temp/`\n"
                f"- 所有文件操作优先使用工作空间内的路径。"
            )
        return prompt + ws_block

    async def init(self) -> None:
        """Initialize agent (memory, subscriptions, skills, etc). Idempotent."""
        if self._base_system_prompt:
            # Already initialized — don't double-register message bus subscription or
            # re-append the system message.
            return
        await self.memory.init_db()
        self._base_system_prompt = self._resolve_system_prompt()
        # Inject workspace path into system prompt so agents know where to
        # read/write files.
        self._base_system_prompt = self._inject_workspace_info(self._base_system_prompt)
        # Only inject system prompt if it isn't already at the head of short_term.
        if not any(m.role == "system" for m in self.memory.short_term):
            self.memory.add_message("system", self._base_system_prompt)
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
        """Activate a skill by name. Invokes handler for custom tool injection.

        Searches both pre-loaded skills and the global registry, allowing
        runtime activation of any registered skill.
        """
        skill = next((s for s in self._skills if s.name == name), None)
        if not skill:
            skill = self.skill_registry.get(name)
            if skill:
                self._skills.append(skill)
        if not skill:
            return False
        self._active_skills.add(name)
        if skill.handler:
            handler_tools = skill.handler(self, self.tool_registry)
            if handler_tools:
                self._skill_tools[name] = [t.name for t in handler_tools]
        self._rebuild_system_prompt()
        return True

    def deactivate_skill(self, name: str) -> bool:
        """Deactivate a skill. Removes handler-injected tools."""
        if name not in self._active_skills:
            return False
        self._active_skills.discard(name)
        # Remove handler-injected tools
        for tool_name in self._skill_tools.pop(name, []):
            self.tool_registry.unregister(tool_name)
        self._rebuild_system_prompt()
        return True

    def deactivate_all_skills(self) -> None:
        """Deactivate all skills, remove handler tools, restore base prompt."""
        for name in list(self._active_skills):
            self.deactivate_skill(name)

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
            self.memory.add_message(
                "assistant",
                response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
            )
            await self._set_state(AgentState.IDLE)
            return response.content
        except Exception as e:
            await self._set_state(AgentState.ERROR)
            error_msg = f"[{self.display_name}] Error: {e}"
            self.memory.add_message("assistant", error_msg)
            return error_msg

    async def chat_stream(self, message: str) -> AsyncIterator[dict]:
        """Streaming chat with tool-call support.

        Yields: {"type": "content", "text": "..."} | {"type": "tool_status", "label": "..."} | {"type": "done"}
        """
        await self._set_state(AgentState.THINKING)
        self.memory.add_message("user", message)
        assistant_stored = False

        try:
            full_content = ""
            for _iteration in range(self._max_tool_rounds):
                messages = self.memory.get_messages()
                tool_names = self._active_tool_names()

                tool_calls_received: list[dict] = []
                streaming_reasoning: str | None = None
                round_content = ""
                async for event in self.llm.stream_with_tools(
                    messages=messages,
                    agent_name=self.name,
                    tools=tool_names or None,
                    tool_registry=self.tool_registry if tool_names else None,
                ):
                    if event.type == "content":
                        full_content += event.text
                        round_content += event.text
                        yield {"type": "content", "text": event.text}
                    elif event.type == "tool_call" and event.tool_call:
                        tool_calls_received.append(event.tool_call)
                    elif event.type == "error":
                        yield {"type": "content", "text": f"\n[Error: {event.text}]"}
                        await self._set_state(AgentState.ERROR)
                        if not assistant_stored:
                            self._pop_last_user_message()
                        return
                    elif event.type == "done":
                        streaming_reasoning = event.reasoning_content

                if not tool_calls_received:
                    self.memory.add_message(
                        "assistant",
                        round_content,
                        reasoning_content=streaming_reasoning,
                    )
                    assistant_stored = True
                    await self._set_state(AgentState.IDLE)
                    yield {"type": "done"}
                    return

                # Record assistant message with tool calls
                self.memory.add_message(
                    "assistant",
                    round_content,
                    tool_calls=tool_calls_received,
                    reasoning_content=streaming_reasoning,
                )
                assistant_stored = True

                for tc in tool_calls_received:
                    tool_name = tc["function"]["name"]
                    raw_args = tc["function"]["arguments"]
                    if isinstance(raw_args, str):
                        try:
                            tool_args = json.loads(raw_args)
                        except json.JSONDecodeError as e:
                            tool_args = None
                            parse_error = f"Invalid JSON in tool call arguments for '{tool_name}': {e}\nRaw arguments: {raw_args}"
                    else:
                        tool_args = raw_args

                    tool_label = (
                        _tool_status_label(tool_name, tool_args)
                        if tool_args
                        else f"{tool_name} (bad args)"
                    )
                    yield {"type": "tool_status", "label": tool_label}

                    if tool_args is None:
                        self.memory.add_message(
                            "tool",
                            parse_error,
                            name=tool_name,
                            tool_call_id=tc["id"],
                        )
                        yield {"type": "tool_done", "label": tool_label, "success": False}
                    else:
                        tool = self.tool_registry.get(tool_name)
                        if tool:
                            await self._set_state(AgentState.ACTING)
                            try:
                                result = await tool.execute(**tool_args)
                            except Exception:
                                result = f"Tool '{tool_name}' execution failed"
                                self.memory.add_message(
                                    "tool",
                                    result,
                                    name=tool_name,
                                    tool_call_id=tc["id"],
                                )
                                yield {"type": "tool_done", "label": tool_label, "success": False}
                                continue
                            self.memory.add_message(
                                "tool",
                                result,
                                name=tool_name,
                                tool_call_id=tc["id"],
                            )
                            yield {"type": "tool_done", "label": tool_label, "success": True}
                        else:
                            self.memory.add_message(
                                "tool",
                                f"Tool '{tool_name}' not found",
                                name=tool_name,
                                tool_call_id=tc["id"],
                            )
                            yield {"type": "tool_done", "label": tool_label, "success": False}
            # Max iterations reached
            if not assistant_stored:
                self._pop_last_user_message()
            await self._set_state(AgentState.IDLE)
            yield {"type": "done"}

        except Exception as e:
            if not assistant_stored:
                self._pop_last_user_message()
            await self._set_state(AgentState.ERROR)
            yield {"type": "content", "text": f"\n[Error: {e}]"}

    def _pop_last_user_message(self) -> None:
        """Remove the most recent user message from short-term memory.

        Used to clean up after an error so the conversation history doesn't
        contain a dangling user message with no assistant response.
        """
        for i in range(len(self.memory.short_term) - 1, -1, -1):
            if self.memory.short_term[i].role == "user":
                self.memory.short_term.pop(i)
                break

    def _active_tool_names(self) -> list[str]:
        """Tool names available to this agent (base + merged from active skills)."""
        names = list(self.tool_names)
        seen = set(names)
        for skill in self._skills:
            if skill.name not in self._active_skills:
                continue
            for tool_name in skill.required_tools:
                if tool_name not in seen:
                    names.append(tool_name)
                    seen.add(tool_name)
        return names

    async def _llm_loop(
        self,
        max_iterations: int = 10,
        on_status: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """LLM reasoning loop with tool calling support."""
        response = LLMResponse(content="")
        tool_names = self._active_tool_names()

        for _ in range(max_iterations):
            messages = self.memory.get_messages()
            if on_status:
                on_status("thinking...")
            response = await self.llm.complete(
                messages=messages,
                agent_name=self.name,
                tools=tool_names or None,
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
                reasoning_content=response.reasoning_content,
            )

            for tc in response.tool_calls:
                tool_name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]
                if isinstance(raw_args, str):
                    try:
                        tool_args = json.loads(raw_args)
                    except json.JSONDecodeError as e:
                        tool_args = None
                        parse_error = f"Invalid JSON in tool call arguments for '{tool_name}': {e}\nRaw arguments: {raw_args}"
                else:
                    tool_args = raw_args

                tool = self.tool_registry.get(tool_name)
                tool_label = (
                    _tool_status_label(tool_name, tool_args)
                    if tool_args
                    else f"{tool_name} (bad args)"
                )

                self.bus.add_event(
                    Event(
                        type=EventType.TOOL_CALL,
                        source=self.name,
                        data={"tool": tool_name, "args": tool_args or {}},
                    )
                )

                if on_status:
                    on_status(tool_label)

                if tool_args is None:
                    self.memory.add_message(
                        "tool",
                        parse_error,
                        name=tool_name,
                        tool_call_id=tc["id"],
                    )
                elif tool:
                    await self._set_state(AgentState.ACTING)
                    result = await tool.execute(**tool_args)
                    self.memory.add_message(
                        "tool",
                        result,
                        name=tool_name,
                        tool_call_id=tc["id"],
                    )
                else:
                    self.memory.add_message(
                        "tool",
                        f"Tool '{tool_name}' not found",
                        name=tool_name,
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
            self.memory.add_message(
                "assistant",
                response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
            )
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
    "lint_file": "Linting {path}",
    "scan_deps": "Scanning {directory}",
    "fetch_page": "Fetching {url}",
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
