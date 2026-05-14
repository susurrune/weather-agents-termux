"""Skill: Security Auditor — vulnerability scanning and risk assessment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from weather_agents.core.skill import Skill
from weather_agents.core.tool import Tool, ToolParameter

if TYPE_CHECKING:
    from weather_agents.core.tool import ToolRegistry


def _make_scan_deps_handler():
    """Create the scan_deps handler — checks Python dependencies for known vulnerabilities."""

    async def scan_deps(directory: str = ".") -> str:
        import os

        expanded = os.path.expanduser(directory)
        if not os.path.isdir(expanded):
            return f"Directory not found: {directory}"

        results = []

        # Check for requirements.txt
        req_path = os.path.join(expanded, "requirements.txt")
        if os.path.isfile(req_path):
            results.append("[requirements.txt] Dependency audit:")
            try:
                with open(req_path, encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        results.append(f"  - {line}")
            except OSError:
                results.append("  (could not read)")

        # Check for pyproject.toml
        pyproj = os.path.join(expanded, "pyproject.toml")
        if os.path.isfile(pyproj):
            results.append("[pyproject.toml] Found — run `pip-audit` for full scan")

        # Try pip-audit if available
        try:
            proc = await _run_subprocess(
                ["pip-audit", "--path", expanded],
                timeout=30,
            )
            if proc.returncode == 0:
                results.append("[pip-audit] No known vulnerabilities found.")
            else:
                results.append(f"[pip-audit]\n{proc.stdout[-500:]}")
        except FileNotFoundError:
            results.append("[pip-audit] Not installed. Install with: pip install pip-audit")
        except Exception:
            pass

        if not results:
            return f"No Python dependency files found in {directory}."
        return "\n".join(results)

    return scan_deps


async def _run_subprocess(cmd: list[str], timeout: int = 30):
    import asyncio
    import subprocess

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode or 1, stdout.decode(), stderr.decode())


def create_skill() -> Skill:
    return Skill(
        name="security_auditor",
        description="Security vulnerability scanning, OWASP top-10 checks, dependency audit",
        required_tools=["read_file", "file_search", "code_search"],
        handler=lambda agent, registry: _inject_scan_tool(registry),
        system_prompt="""## Skill: Security Auditor
You have activated the Security Auditor skill. In this mode:
1. Check against OWASP Top 10 categories:
   - Injection (SQL, Command, NoSQL)
   - Broken Authentication & Session Management
   - Sensitive Data Exposure
   - XML External Entities (XXE)
   - Broken Access Control
   - Security Misconfiguration
   - Cross-Site Scripting (XSS)
   - Insecure Deserialization
   - Using Components with Known Vulnerabilities
   - Insufficient Logging & Monitoring
2. Label each vulnerability with risk level and CVSS reference
3. Provide concrete remediation steps with code examples
4. Check dependencies for known CVEs using the `scan_deps` tool""",
    )


def _inject_scan_tool(registry: ToolRegistry) -> list[Tool]:
    tool = Tool(
        name="scan_deps",
        description="Scan Python dependencies for known vulnerabilities. Checks requirements.txt and runs pip-audit if available.",
        parameters=[
            ToolParameter(
                name="directory",
                type="string",
                description="Project directory to scan (default: current directory)",
                required=False,
            ),
        ],
        handler=_make_scan_deps_handler(),
    )
    registry.register(tool)
    return [tool]
