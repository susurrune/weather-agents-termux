"""Built-in tool implementations."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import httpx

from weather_agents.core.tool import Tool, ToolParameter, global_registry


# -- File Tools --

async def _read_file(path: str, **kwargs) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()[:50000]  # Limit output
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


async def _write_file(path: str, content: str, **kwargs) -> str:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


async def _edit_file(path: str, old_text: str, new_text: str, **kwargs) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        content = content.replace(old_text, new_text, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully edited {path}"
    except Exception as e:
        return f"Error editing file: {e}"


# -- Search Tools --

async def _file_search(directory: str, pattern: str, **kwargs) -> str:
    import glob
    matches = glob.glob(f"{directory}/**/{pattern}", recursive=True)
    if not matches:
        return f"No files matching '{pattern}' found in {directory}"
    return "\n".join(matches[:50])


async def _code_search(directory: str, query: str, **kwargs) -> str:
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.ts", "--include=*.js",
             "--include=*.go", "--include=*.rs", query, directory],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout[:10000] if result.stdout else "No matches found"
        return output
    except Exception as e:
        return f"Error searching: {e}"


# -- Shell Tool (safe mode) --

_BLOCKED_COMMANDS = {
    "dd", "mkfs", "fdisk", "parted", "shutdown", "reboot", "init", "poweroff",
    "halt", "grub-mkconfig", "update-grub", "passwd", "adduser", "userdel",
}
_DANGEROUS_ARGS = {"/", "-rf /", "--delete /", ":/"}


async def _shell_exec(command: str, timeout: int = 30, **kwargs) -> str:
    """Execute a shell command safely using argument list form."""
    try:
        args = shlex.split(command)
    except ValueError as e:
        return f"Invalid command syntax: {e}"
    if not args:
        return "Empty command."

    base = os.path.basename(args[0]).lower()
    if base in _BLOCKED_COMMANDS:
        return f"Blocked: '{base}' is not allowed for security reasons."

    # Check for dangerous argument patterns
    for arg in args[1:]:
        if arg in _DANGEROUS_ARGS:
            return f"Blocked: dangerous argument '{arg}'"
        # Check for rm with recursive force on root
        if base == "rm" and arg in ("-rf /", "-fr /", "--recursive --force /"):
            return "Blocked: recursive root deletion"
        if base == "chmod" and arg == "777 /":
            return "Blocked: unsafe permission change on root"

    try:
        result = subprocess.run(
            args,  # ←列表形式，没有 shell=True
            capture_output=True, text=True, timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout[:20000]
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr[:5000]}"
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output or "Command completed with no output."
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except OSError as e:
        return f"Command not found or not executable: {e}"
    except Exception as e:
        return f"Error executing command: {e}"


# -- HTTP Tools (shared connection pool) --

_http_client: httpx.AsyncClient | None = None


async def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=15, limits=httpx.Limits(max_keepalive_connections=10))
    return _http_client


async def _http_get(url: str, **kwargs) -> str:
    try:
        client = await _get_http()
        resp = await client.get(url)
        return f"Status: {resp.status_code}\n{resp.text[:20000]}"
    except Exception as e:
        return f"Error: {e}"


async def _http_post(url: str, data: str = "", **kwargs) -> str:
    try:
        client = await _get_http()
        resp = await client.post(url, content=data)
        return f"Status: {resp.status_code}\n{resp.text[:20000]}"
    except Exception as e:
        return f"Error: {e}"


# -- Web Search (placeholder) --

async def _web_search(query: str, **kwargs) -> str:
    return f"Web search for '{query}' - configure a search API key to enable real search results."


# -- Register all tools --

_registered = False


def register_builtin_tools() -> None:
    global _registered
    if _registered:
        return
    _registered = True

    tools = [
        Tool(
            name="read_file",
            description="Read the contents of a file",
            parameters=[
                ToolParameter(name="path", type="string", description="File path to read"),
            ],
            handler=_read_file,
        ),
        Tool(
            name="write_file",
            description="Write content to a file",
            parameters=[
                ToolParameter(name="path", type="string", description="File path to write"),
                ToolParameter(name="content", type="string", description="Content to write"),
            ],
            handler=_write_file,
        ),
        Tool(
            name="edit_file",
            description="Edit a file by replacing text",
            parameters=[
                ToolParameter(name="path", type="string", description="File path to edit"),
                ToolParameter(name="old_text", type="string", description="Text to find"),
                ToolParameter(name="new_text", type="string", description="Replacement text"),
            ],
            handler=_edit_file,
        ),
        Tool(
            name="file_search",
            description="Search for files matching a pattern",
            parameters=[
                ToolParameter(name="directory", type="string", description="Directory to search in"),
                ToolParameter(name="pattern", type="string", description="Glob pattern to match"),
            ],
            handler=_file_search,
        ),
        Tool(
            name="code_search",
            description="Search for code patterns using grep",
            parameters=[
                ToolParameter(name="directory", type="string", description="Directory to search"),
                ToolParameter(name="query", type="string", description="Search query"),
            ],
            handler=_code_search,
        ),
        Tool(
            name="shell_exec",
            description="Execute a shell command",
            parameters=[
                ToolParameter(name="command", type="string", description="Shell command to execute"),
                ToolParameter(name="timeout", type="number", description="Timeout in seconds",
                              required=False, default=30),
            ],
            handler=_shell_exec,
        ),
        Tool(
            name="http_get",
            description="Make an HTTP GET request",
            parameters=[
                ToolParameter(name="url", type="string", description="URL to request"),
            ],
            handler=_http_get,
        ),
        Tool(
            name="http_post",
            description="Make an HTTP POST request",
            parameters=[
                ToolParameter(name="url", type="string", description="URL to post to"),
                ToolParameter(name="data", type="string", description="Request body",
                              required=False, default=""),
            ],
            handler=_http_post,
        ),
        Tool(
            name="web_search",
            description="Search the web for information",
            parameters=[
                ToolParameter(name="query", type="string", description="Search query"),
            ],
            handler=_web_search,
        ),
    ]

    for tool in tools:
        global_registry.register(tool)
