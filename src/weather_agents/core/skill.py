"""Skill system — composable capability modules for agents.

Each Skill is a self-contained module that extends an agent's system prompt
and tool set when activated. Agents come with pre-installed skills matching
their specialty.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Skill:
    """A composable capability module for an agent.

    Attributes:
        name: Unique identifier (e.g. "web_research").
        description: Human-readable summary of what the skill does.
        system_prompt: System prompt text injected when the skill is active.
        required_tools: Tool names this skill needs available.
        handler: Optional custom execution logic beyond tool calling.
    """

    name: str
    description: str
    system_prompt: str = ""
    required_tools: list[str] = field(default_factory=list)
    handler: Callable[..., Any] | None = None


class SkillRegistry:
    """Central registry for all available skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def get_skills(self, names: list[str] | None = None) -> list[Skill]:
        if names is None:
            return list(self._skills.values())
        return [self._skills[n] for n in names if n in self._skills]

    def list_names(self) -> list[str]:
        return list(self._skills.keys())

    def merge(self, other: SkillRegistry) -> None:
        self._skills.update(other._skills)


# Global skill registry
global_skill_registry = SkillRegistry()
