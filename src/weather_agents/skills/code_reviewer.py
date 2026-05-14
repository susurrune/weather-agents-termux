"""Skill: Code Reviewer — systematic code review with severity grading."""

from __future__ import annotations

from typing import TYPE_CHECKING

from weather_agents.core.skill import Skill
from weather_agents.core.tool import Tool, ToolParameter

if TYPE_CHECKING:
    from weather_agents.core.tool import ToolRegistry


def _make_lint_file_handler():
    """Create the lint_file handler — checks a file for common Python issues."""

    async def lint_file(path: str) -> str:
        import ast
        import os

        expanded = os.path.expanduser(path)
        if not os.path.isfile(expanded):
            return f"File not found: {path}"

        try:
            with open(expanded, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=expanded)
        except SyntaxError as e:
            return f"Syntax error at line {e.lineno}: {e.msg}"

        issues = []

        for node in ast.walk(tree):
            # Detect bare except clauses
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                issues.append(f"[WARN] line {node.lineno}: bare except clause")

            # Detect print() usage (debug leftover)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                issues.append(f"[INFO] line {node.lineno}: print() call — verify if intentional")

            # Detect use of eval/exec
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("eval", "exec")
            ):
                issues.append(
                    f"[CRITICAL] line {node.lineno}: {node.func.id}() — potential code injection"
                )

        if not issues:
            return f"Lint passed for {path}. No issues detected."
        return "\n".join(issues)

    return lint_file


def create_skill() -> Skill:
    return Skill(
        name="code_reviewer",
        description="Systematic code review, bug detection, style checking, best-practice validation",
        required_tools=["read_file", "file_search", "code_search"],
        handler=lambda agent, registry: _inject_lint_tool(registry),
        system_prompt="""## Skill: Code Reviewer
You have activated the Code Reviewer skill. In this mode:
1. Review code across these dimensions:
   - Correctness — logic errors, boundary conditions, concurrency issues
   - Maintainability — naming, structure, complexity
   - Security — injection, XSS, sensitive data exposure
   - Performance — algorithm efficiency, resource leaks
2. Tag each issue with severity level
3. Provide concrete fix suggestions with code examples
4. End with an overall score and prioritized fix list
5. Use the `lint_file` tool for automated static analysis before manual review""",
    )


def _inject_lint_tool(registry: ToolRegistry) -> list[Tool]:
    tool = Tool(
        name="lint_file",
        description="Run static analysis on a Python file to detect common issues (bare except, eval, print calls, etc.)",
        parameters=[
            ToolParameter(
                name="path",
                type="string",
                description="Path to the Python file to lint",
                required=True,
            ),
        ],
        handler=_make_lint_file_handler(),
    )
    registry.register(tool)
    return [tool]
