"""Built-in tool implementations."""

from __future__ import annotations

import os
import shlex
import subprocess
from urllib.parse import urlparse

import httpx

from weather_agents.core.tool import Tool, ToolParameter, global_registry


# -- File Tools --

async def _read_file(path: str, **kwargs) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()[:50000]
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


async def _write_file(path: str, content: str, **kwargs) -> str:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
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

async def _list_directory(path: str = ".", **kwargs) -> str:
    """List files and directories with basic metadata."""
    try:
        entries = []
        for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name)):
            if entry.is_dir():
                entries.append(f"  [dir]  {entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                    if size < 1024:
                        size_str = f"{size}B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f}KB"
                    else:
                        size_str = f"{size / 1024 / 1024:.1f}MB"
                    entries.append(f"  {size_str:>8}  {entry.name}")
                except OSError:
                    entries.append(f"           {entry.name}")
        if not entries:
            return f"Empty directory: {path}"
        header = f"Directory: {os.path.abspath(path)} ({len(entries)} items)\n"
        return header + "\n".join(entries)
    except FileNotFoundError:
        return f"Error: Directory not found: {path}"
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error listing directory: {e}"


async def _tree(directory: str = ".", max_depth: int = 3, **kwargs) -> str:
    """Show directory tree structure."""
    lines = []
    try:
        base = os.path.abspath(directory)
        lines.append(base)
        _tree_walk(base, "", lines, 0, int(max_depth))
        if len(lines) > 200:
            lines = lines[:200]
            lines.append("... (truncated)")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def _tree_walk(path: str, prefix: str, lines: list, depth: int, max_depth: int) -> None:
    if depth >= max_depth or len(lines) > 200:
        return
    try:
        entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name))
    except PermissionError:
        return
    # Filter hidden dirs
    entries = [e for e in entries if not e.name.startswith(".")]
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "+-" if is_last else "|-"
        lines.append(f"{prefix}{connector} {entry.name}{'/' if entry.is_dir() else ''}")
        if entry.is_dir():
            extension = "   " if is_last else "|  "
            _tree_walk(entry.path, prefix + extension, lines, depth + 1, max_depth)


async def _move_file(src: str, dst: str, **kwargs) -> str:
    """Move or rename a file or directory."""
    try:
        import shutil
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.move(src, dst)
        return f"Moved: {src} -> {dst}"
    except Exception as e:
        return f"Error moving: {e}"


async def _copy_file(src: str, dst: str, **kwargs) -> str:
    """Copy a file or directory."""
    try:
        import shutil
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return f"Copied: {src} -> {dst}"
    except Exception as e:
        return f"Error copying: {e}"


async def _delete_file(path: str, **kwargs) -> str:
    """Delete a file or empty directory (non-recursive for safety)."""
    try:
        if os.path.isdir(path):
            os.rmdir(path)
            return f"Deleted directory: {path}"
        else:
            os.remove(path)
            return f"Deleted: {path}"
    except OSError as e:
        return f"Error deleting: {e}"


async def _get_cwd(**kwargs) -> str:
    """Return current working directory."""
    return os.getcwd()


async def _file_search(directory: str, pattern: str, **kwargs) -> str:
    import glob

    matches = glob.glob(f"{directory}/**/{pattern}", recursive=True)
    if not matches:
        return f"No files matching '{pattern}' found in {directory}"
    return "\n".join(matches[:50])


async def _code_search(directory: str, query: str, **kwargs) -> str:
    try:
        result = subprocess.run(
            [
                "grep", "-rn",
                "--include=*.py", "--include=*.ts", "--include=*.js",
                "--include=*.go", "--include=*.rs", "--include=*.java",
                "--include=*.yaml", "--include=*.json",
                query, directory,
            ],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout[:10000] if result.stdout else "No matches found"
        return output
    except FileNotFoundError:
        # grep not available, fall back to Python
        import re
        matches = []
        for root, _, files in os.walk(directory):
            for f in files:
                if not f.endswith((".py", ".ts", ".js", ".go", ".rs", ".java")):
                    continue
                fp = os.path.join(root, f)
                try:
                    with open(fp, encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, 1):
                            if query in line:
                                matches.append(f"{fp}:{i}:{line.rstrip()}")
                                if len(matches) >= 50:
                                    return "\n".join(matches)
                except OSError:
                    continue
        return "\n".join(matches) if matches else "No matches found"
    except Exception as e:
        return f"Error searching: {e}"


# -- Shell Tool (safe mode) --

_BLOCKED_COMMANDS = {
    "dd", "mkfs", "fdisk", "parted", "shutdown", "reboot", "init", "poweroff",
    "halt", "grub-mkconfig", "update-grub", "passwd", "adduser", "userdel",
    "format",
}


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

    # Block recursive deletion on root-like paths
    if base == "rm" and any(a in ("-rf", "-fr", "--recursive") for a in args):
        for a in args[1:]:
            normalized = os.path.normpath(a) if "/" in a else a
            if normalized in ("/", "C:\\", "\\"):
                return "Blocked: recursive root deletion"

    try:
        result = subprocess.run(
            args,
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


# -- HTTP Tools --

_http_client: httpx.AsyncClient | None = None


async def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_keepalive_connections=10),
            follow_redirects=True,
            headers={"User-Agent": "WeatherAgents/1.0"},
        )
    return _http_client


async def _http_get(url: str, **kwargs) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"Error: Invalid URL: {url}"
    try:
        client = await _get_http()
        resp = await client.get(url)
        return f"Status: {resp.status_code}\n{resp.text[:20000]}"
    except Exception as e:
        return f"Error: {e}"


async def _http_post(url: str, data: str = "", **kwargs) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"Error: Invalid URL: {url}"
    try:
        client = await _get_http()
        headers = {}
        # Auto-detect JSON
        if data.strip().startswith(("{", "[")):
            headers["Content-Type"] = "application/json"
        resp = await client.post(url, content=data, headers=headers)
        return f"Status: {resp.status_code}\n{resp.text[:20000]}"
    except Exception as e:
        return f"Error: {e}"


# -- Web Search (DuckDuckGo HTML scraping) --

async def _web_search(query: str, num_results: int = 5, **kwargs) -> str:
    """Search the web using DuckDuckGo HTML endpoint."""
    try:
        client = await _get_http()
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; WeatherAgents/1.0)"},
        )
        if resp.status_code != 200:
            return f"Search failed with status {resp.status_code}"

        # Parse results from HTML
        results = _parse_ddg_results(resp.text, num_results)
        if not results:
            return f"No results found for '{query}'"

        output_parts = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            output_parts.append(f"{i}. {r['title']}")
            output_parts.append(f"   {r['url']}")
            if r.get("snippet"):
                output_parts.append(f"   {r['snippet']}")
            output_parts.append("")
        return "\n".join(output_parts)
    except Exception as e:
        return f"Search error: {e}"


def _parse_ddg_results(html: str, max_results: int) -> list[dict]:
    """Extract search results from DuckDuckGo HTML response."""
    import re

    results = []
    # DuckDuckGo HTML results are in <a class="result__a"> tags
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        if len(results) >= max_results:
            break
        url = match.group(1)
        title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()

        # DuckDuckGo wraps URLs in a redirect
        if "uddg=" in url:
            from urllib.parse import parse_qs, unquote
            try:
                qs = parse_qs(urlparse(url).query)
                url = unquote(qs.get("uddg", [url])[0])
            except Exception:
                pass

        if title:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


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
            description="Read the contents of a text file (max 50KB)",
            parameters=[
                ToolParameter(name="path", type="string", description="File path to read"),
            ],
            handler=_read_file,
        ),
        Tool(
            name="write_file",
            description="Write content to a file, creating parent directories if needed",
            parameters=[
                ToolParameter(name="path", type="string", description="File path to write"),
                ToolParameter(name="content", type="string", description="Content to write"),
            ],
            handler=_write_file,
        ),
        Tool(
            name="edit_file",
            description="Edit a file by replacing the first occurrence of old_text with new_text",
            parameters=[
                ToolParameter(name="path", type="string", description="File path to edit"),
                ToolParameter(name="old_text", type="string", description="Text to find"),
                ToolParameter(name="new_text", type="string", description="Replacement text"),
            ],
            handler=_edit_file,
        ),
        Tool(
            name="file_search",
            description="Search for files matching a glob pattern recursively",
            parameters=[
                ToolParameter(name="directory", type="string", description="Directory to search in"),
                ToolParameter(name="pattern", type="string", description="Glob pattern to match"),
            ],
            handler=_file_search,
        ),
        Tool(
            name="code_search",
            description="Search for text patterns in source code files",
            parameters=[
                ToolParameter(name="directory", type="string", description="Directory to search"),
                ToolParameter(name="query", type="string", description="Search query"),
            ],
            handler=_code_search,
        ),
        Tool(
            name="shell_exec",
            description="Execute a shell command safely (dangerous commands are blocked)",
            parameters=[
                ToolParameter(name="command", type="string", description="Shell command to execute"),
                ToolParameter(
                    name="timeout", type="number", description="Timeout in seconds",
                    required=False, default=30,
                ),
            ],
            handler=_shell_exec,
        ),
        Tool(
            name="http_get",
            description="Make an HTTP GET request and return status + body",
            parameters=[
                ToolParameter(name="url", type="string", description="URL to request"),
            ],
            handler=_http_get,
        ),
        Tool(
            name="http_post",
            description="Make an HTTP POST request with optional body data",
            parameters=[
                ToolParameter(name="url", type="string", description="URL to post to"),
                ToolParameter(
                    name="data", type="string", description="Request body",
                    required=False, default="",
                ),
            ],
            handler=_http_post,
        ),
        Tool(
            name="web_search",
            description="Search the web using DuckDuckGo and return top results with titles, URLs, and snippets",
            parameters=[
                ToolParameter(name="query", type="string", description="Search query"),
                ToolParameter(
                    name="num_results", type="number", description="Number of results (default 5)",
                    required=False, default=5,
                ),
            ],
            handler=_web_search,
        ),
        Tool(
            name="list_directory",
            description="List files and directories with sizes. Defaults to current directory.",
            parameters=[
                ToolParameter(
                    name="path", type="string", description="Directory path (default: '.')",
                    required=False, default=".",
                ),
            ],
            handler=_list_directory,
        ),
        Tool(
            name="tree",
            description="Show directory tree structure (non-hidden files, configurable depth)",
            parameters=[
                ToolParameter(
                    name="directory", type="string", description="Root directory (default: '.')",
                    required=False, default=".",
                ),
                ToolParameter(
                    name="max_depth", type="number", description="Max depth (default: 3)",
                    required=False, default=3,
                ),
            ],
            handler=_tree,
        ),
        Tool(
            name="move_file",
            description="Move or rename a file or directory",
            parameters=[
                ToolParameter(name="src", type="string", description="Source path"),
                ToolParameter(name="dst", type="string", description="Destination path"),
            ],
            handler=_move_file,
        ),
        Tool(
            name="copy_file",
            description="Copy a file or directory tree",
            parameters=[
                ToolParameter(name="src", type="string", description="Source path"),
                ToolParameter(name="dst", type="string", description="Destination path"),
            ],
            handler=_copy_file,
        ),
        Tool(
            name="delete_file",
            description="Delete a file or empty directory (non-recursive for safety)",
            parameters=[
                ToolParameter(name="path", type="string", description="File or directory to delete"),
            ],
            handler=_delete_file,
        ),
        Tool(
            name="get_cwd",
            description="Get the current working directory path",
            parameters=[],
            handler=_get_cwd,
        ),
    ]

    for tool in tools:
        global_registry.register(tool)
