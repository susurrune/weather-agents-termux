"""CLI interface for Weather Agents — terminal agent product."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from typing import Any

import typer
from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    import msvcrt as _msvcrt
else:
    import termios as _termios
    import tty as _tty

from weather_agents import __version__
from weather_agents.core.config import (
    USER_CONFIG_DIR,
    _sync_api_keys_to_env,
    delete_config,
    format_models_for_display,
    load_config,
    load_model_catalog,
    set_config,
)
from weather_agents.core.factory import (
    AGENT_CLASSES,
    AGENT_COLORS,
    AGENT_EMOJI,
    create_system_context,
)
from weather_agents.core.workspace import (
    detect_best_workspace_root,
    format_bytes,
    init_workspace,
    resolve_workspace_path,
)

# ── Slash commands registry (for popup) ──────────────────────────────────

_COMMANDS: list[tuple[str, str]] = [
    ("/help", "show all commands"),
    ("/clear", "clear screen"),
    ("/status", "agent overview"),
    ("/cost", "usage & cost"),
    ("/cost reset", "reset cost counter"),
    ("/compact", "compress context"),
    ("/history", "event log"),
    ("/mcp", "MCP server status"),
    ("/skills", "list skills"),
    ("/use ", "activate a skill"),
    ("/deactivate", "deactivate skills"),
    ("/sessions", "list sessions"),
    ("/session new ", "start new session"),
    ("/session load ", "switch session"),
    ("/session delete ", "delete session"),
    ("/memory", "memory stats"),
    ("/memory clear", "clear short-term memory"),
    ("/workspace", "workspace info"),
    ("/workspace set ", "set workspace path"),
    ("/workspace auto", "auto-detect workspace"),
    ("/model", "view/change model"),
    ("/model ", "set per-agent model"),
    ("/apikey", "manage API keys"),
    ("/apikey set ", "add/replace API key"),
    ("/apikey del ", "remove API key"),
    ("/task ", "multi-agent orchestration"),
    ("/fog", "switch to Fog"),
    ("/rain", "switch to Rain"),
    ("/frost", "switch to Frost"),
    ("/snow", "switch to Snow"),
    ("/dew", "switch to Dew"),
    ("/version", "version info"),
    ("/quit", "exit chat"),
]

_COMMAND_LOOKUP: dict[str, str] = {c[0].split()[0].lstrip("/"): c[0] for c in _COMMANDS}

# ── Cross-platform key reader ─────────────────────────────────────────────


def _get_key() -> str:
    """Read a single keypress. Returns named tokens for special keys."""
    if sys.platform == "win32":
        ch = _msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = _msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(ch2, ch2)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x1b":
            return "esc"
        if ch == "\r":
            return "enter"
        if ch == "\x08":
            return "backspace"
        if ch == "\t":
            return "tab"
        return ch
    else:
        fd = sys.stdin.fileno()
        old = _termios.tcgetattr(fd)
        try:
            _tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                nxt = sys.stdin.read(2)
                if nxt == "[A":
                    return "up"
                if nxt == "[B":
                    return "down"
                if nxt == "[C":
                    return "right"
                if nxt == "[D":
                    return "left"
                if nxt in ("[Z", "OQ", "OP", "OQ"):
                    return "tab"
                return "esc"
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\r":
                return "enter"
            if ch in ("\x7f", "\x08"):
                return "backspace"
            if ch == "\t":
                return "tab"
            return ch
        finally:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old)


app = typer.Typer(name="wa", help="Weather Agents CLI", no_args_is_help=True)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"Weather Agents v{__version__}")
        raise typer.Exit()


@app.callback()
def _global_options(
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Top-level Typer callback hosting global flags like --version."""
    _ = version  # Consumed by callback above.


# -- Spinner + display helpers -----------------------------------------------

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_spin_idx = 0


def _next_spin() -> str:
    global _spin_idx
    c = _SPINNER[_spin_idx % len(_SPINNER)]
    _spin_idx += 1
    return c


def _build_stream_display(
    agent,
    status_text: str,
    md_content: str,
    activities: list[dict],
) -> Table:
    """Build the Live renderable during streaming: spinner + content + tool activity."""
    color = AGENT_COLORS.get(agent.name, "white")
    spin = _next_spin()

    tbl = Table(show_header=False, box=None, padding=0, expand=True)
    tbl.add_column(ratio=1)

    # Header row: spinner · agent · status
    header = Text()
    header.append(f"  {spin} ", style="dim")
    header.append(f"{agent.emoji} ", style=f"bold {color}")
    header.append(agent.display_name, style=f"bold {color}")
    if status_text:
        header.append("  ·  ", style="dim")
        header.append(status_text, style="dim")
    tbl.add_row(header)

    # Thin separator
    tbl.add_row(Text("  " + "─" * min(console.width - 4, 78), style="dim"))

    # Streamed content
    if md_content:
        tbl.add_row(Padding(Markdown(md_content), pad=(0, 2, 0, 2)))

    # Tool activity lines (most recent 6)
    if activities:
        tbl.add_row(Text(""))
        for a in activities[-6:]:
            s = a["status"]
            if s == "done":
                icon, label_style = "[green]✓[/green]", "dim"
            elif s == "error":
                icon, label_style = "[red]✗[/red]", "red dim"
            else:
                icon, label_style = "[cyan]⠿[/cyan]", "default"
            row = Text()
            row.append("    ")
            row.append(Text.from_markup(icon))
            row.append("  ")
            row.append(a["label"], style=label_style)
            tbl.add_row(row)

    return tbl


def _build_response_panel(
    agent,
    content: str,
    elapsed: float,
    interrupted: bool = False,
) -> Panel:
    """Build a Panel for the final agent response."""
    color = AGENT_COLORS.get(agent.name, "white")
    title_text = Text()
    title_text.append(f"{agent.emoji} ", style=f"bold {color}")
    title_text.append(agent.display_name, style=f"bold {color}")

    sub_parts = [f"{elapsed:.1f}s"]
    if interrupted:
        sub_parts.append("interrupted")
    subtitle = "  ".join(sub_parts)

    return Panel(
        Padding(Markdown(content), pad=(0, 1, 0, 1)),
        title=title_text,
        title_align="left",
        subtitle=f"[dim]{subtitle}[/dim]",
        subtitle_align="right",
        border_style=f"dim {color}",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def _build_status_line(agent, ctx) -> Text:
    """Build a compact status line: context bar · msgs · cost · model."""
    line = Text()
    try:
        cu = agent.context_usage()
        model = cu["model"]
        pct = cu["pct"]
        est = cu["estimated_tokens"]
        max_ctx = cu["max_tokens"]
        msgs = cu["message_count"]
        cost = ctx.llm.get_total_cost()

        filled = min(10, max(0, int(pct / 10)))
        bar_color = "green" if pct < 50 else "yellow" if pct < 80 else "red"
        bar = f"{'█' * filled}{'░' * (10 - filled)}"
        ctx_str = f"{est // 1000}k/{max_ctx // 1000}k" if est > 1000 else f"{est}/{max_ctx}"

        line.append(bar, style=bar_color)
        line.append(f" {pct}% {ctx_str}", style="dim")
        line.append("  ·  ", style="dim")
        line.append(f"{msgs} msgs", style="dim")
        line.append("  ·  ", style="dim")
        line.append(f"${cost:.4f}", style="dim green" if cost < 0.01 else "dim yellow")
        line.append("  ·  ", style="dim")
        # Truncate long model names
        model_short = model if len(model) <= 30 else model[:27] + "…"
        line.append(model_short, style="dim")
    except Exception:
        line.append("", style="dim")
    return line


# -- Chat -------------------------------------------------------------------


async def _chat_single(agent_name: str, message: str) -> None:
    ctx = create_system_context()
    agent = ctx.agent_map.get(agent_name)
    if not agent:
        console.print(f"[red]Unknown agent: {agent_name}[/red]")
        return
    await _init_agent_lazy(agent, ctx)
    try:
        t0 = time.monotonic()
        status_handle = console.status(
            f"[dim]{agent.emoji} {agent.display_name} thinking...[/dim]",
            spinner="dots",
        )
        status_handle.start()

        def _on_status(msg: str) -> None:
            status_handle.update(f"[dim]{agent.emoji} {msg}[/dim]")

        try:
            resp = await agent.chat(message, on_status=_on_status)
        finally:
            status_handle.stop()
        elapsed = time.monotonic() - t0
        console.print(_build_response_panel(agent, resp, elapsed))
    finally:
        await ctx.close_all()


async def _init_agent_lazy(agent, ctx) -> None:
    """Init an agent if not already initialized. Used for lazy startup."""
    if not agent._base_system_prompt:
        await agent.init()
        # Init MCP if configured (only on first agent init)
        if ctx.mcp is not None and ctx.mcp._server_configs and not ctx.mcp_status:
            with contextlib.suppress(Exception):
                ctx.mcp_status = await ctx.mcp.connect_all()


def _build_input_display(
    agent,
    ctx,
    buffer: str,
    popup_visible: bool,
    selected_idx: int,
    filtered_commands: list[tuple[str, str]],
) -> list:
    """Build renderables for the input area with optional command popup."""
    color = AGENT_COLORS.get(agent.name, "cyan")
    results: list = []
    w = console.width

    # ── Status bar ─────────────────────────────────────────────────────────
    status_text = _build_status_line(agent, ctx)
    status_bar = Text()
    status_bar.append("  ")
    status_bar.append(status_text)
    results.append(status_bar)

    # Thin separator
    results.append(Text("  " + "─" * max(0, w - 4), style="dim"))

    # ── Prompt line ────────────────────────────────────────────────────────
    prompt = Text()
    prompt.append("  ")
    prompt.append(f"{agent.emoji} ", style=f"bold {color}")
    prompt.append(agent.display_name, style=f"bold {color}")
    prompt.append(" ❯ ", style=f"{color}")
    if buffer:
        if buffer.startswith("/"):
            space_idx = buffer.find(" ")
            if space_idx > 0:
                prompt.append(buffer[:space_idx], style="bold cyan")
                prompt.append(buffer[space_idx:])
            else:
                prompt.append(buffer, style="bold cyan")
        else:
            prompt.append(buffer)
    prompt.append("▌", style=f"bold {color}")  # blinking-style cursor
    results.append(prompt)

    # ── Hint line ──────────────────────────────────────────────────────────
    if popup_visible:
        hint = "↑↓ select  tab complete  esc dismiss  enter confirm"
    else:
        hint = "/ commands  ↑↓ history  esc clear"
    hint_line = Text()
    hint_line.append("  ")
    hint_line.append(hint, style="dim")
    results.append(hint_line)

    # ── Command popup ──────────────────────────────────────────────────────
    if popup_visible and filtered_commands:
        tbl = Table(show_header=False, box=None, padding=(0, 1), expand=False)
        tbl.add_column(width=28)
        tbl.add_column(style="dim")

        start = max(0, min(selected_idx - 6, len(filtered_commands) - 14))
        end = min(len(filtered_commands), start + 14)

        if start > 0:
            tbl.add_row(Text("  ↑ more", style="dim"), "")

        for i in range(start, end):
            cmd, desc = filtered_commands[i]
            if i == selected_idx:
                cmd_text = Text()
                cmd_text.append("❯ ", style="bold cyan")
                cmd_text.append(cmd, style="bold cyan")
                tbl.add_row(cmd_text, Text(desc, style="default"))
            else:
                cmd_text = Text()
                cmd_text.append("  ")
                cmd_text.append(cmd, style="cyan")
                tbl.add_row(cmd_text, Text(desc, style="dim"))

        if end < len(filtered_commands):
            tbl.add_row(Text("  ↓ more", style="dim"), "")

        popup = Panel(
            tbl,
            title="[dim]commands[/dim]",
            title_align="left",
            border_style="dim cyan",
            box=box.ROUNDED,
            padding=(0, 0),
            width=min(60, w - 4),
        )
        results.append(popup)

    return results


def _read_line_with_popup(agent, ctx) -> str:
    """Read a line of input with slash-command popup support."""
    # Fall back to simple input when stdin is not a TTY (piped / test env)
    if not sys.stdin.isatty():
        color = AGENT_COLORS.get(agent.name, "cyan")
        prompt = Text()
        prompt.append(f"  {agent.emoji} ", style=f"bold {color}")
        prompt.append(agent.display_name, style=f"bold {color}")
        prompt.append(" ❯ ", style=color)
        return console.input(prompt)

    buffer: list[str] = []
    popup_visible = False
    selected_idx = 0

    result = ""
    with Live(
        Table(show_header=False, box=None, padding=0),
        console=console,
        refresh_per_second=30,
        transient=True,
    ) as live:
        while True:
            text = "".join(buffer)
            filtered = [c for c in _COMMANDS if c[0].startswith(text)] if popup_visible else []
            if filtered and selected_idx >= len(filtered):
                selected_idx = len(filtered) - 1

            tbl = Table(show_header=False, box=None, padding=0, expand=True)
            tbl.add_column(ratio=1)
            for item in _build_input_display(
                agent, ctx, text, popup_visible, selected_idx, filtered
            ):
                tbl.add_row(item)
            live.update(tbl)

            try:
                key = _get_key()
            except KeyboardInterrupt:
                raise

            if key == "enter":
                if popup_visible and filtered:
                    result = filtered[selected_idx][0]
                else:
                    result = "".join(buffer).strip()
                if result:
                    break
                continue

            if key == "esc":
                if popup_visible:
                    popup_visible = False
                    buffer.clear()
                    selected_idx = 0
                else:
                    buffer.clear()
                continue

            if key == "backspace":
                if buffer:
                    buffer.pop()
                    if not buffer:
                        popup_visible = False
                        selected_idx = 0
                continue

            if popup_visible and filtered:
                if key == "up":
                    selected_idx = max(0, selected_idx - 1)
                elif key == "down":
                    selected_idx = min(len(filtered) - 1, selected_idx + 1)
                elif key == "tab":
                    buffer[:] = list(filtered[selected_idx][0])
                    if not filtered[selected_idx][0].endswith(" "):
                        buffer.append(" ")
                    popup_visible = False
                elif isinstance(key, str) and len(key) == 1 and key.isprintable():
                    buffer.append(key)
                    selected_idx = 0
                continue

            if isinstance(key, str) and len(key) == 1:
                if key == "/" and not buffer:
                    buffer.append(key)
                    popup_visible = True
                    selected_idx = 0
                elif key.isprintable():
                    buffer.append(key)
                    if buffer == ["/"]:
                        popup_visible = True
                        selected_idx = 0
                    elif popup_visible:
                        selected_idx = 0

    if result:
        color = AGENT_COLORS.get(agent.name, "cyan")
        echo = Text()
        echo.append(f"  {agent.emoji} ", style=f"bold {color}")
        echo.append(agent.display_name, style=f"bold {color}")
        echo.append(" ❯ ", style=color)
        echo.append(result, style="white")
        console.print(echo)
    return result


async def _interactive(agent_name: str | None = None) -> None:
    ctx = create_system_context()
    # Lazy init: only initialize current agent, not all 5
    current = agent_name or "fog"
    agent = ctx.agent_map[current]
    await _init_agent_lazy(agent, ctx)
    model = ctx.config.llm.default_model
    ws = getattr(ctx, "workspace_path", "")
    workspace_path = ws if isinstance(ws, str) else ""
    _print_welcome(model, workspace_path)

    try:
        agents = ctx.agent_map

        while True:
            try:
                inp = _read_line_with_popup(agent, ctx)
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            if not inp:
                continue

            cmd = inp.strip()
            cmd_lower = cmd.lower()

            # --- Slash commands ---
            if cmd_lower in ("/quit", "/exit", "/q"):
                break
            if cmd_lower in ("/help", "/?"):
                _print_help()
                continue
            if cmd_lower == "/clear":
                console.clear()
                _print_welcome(ctx.config.llm.default_model, workspace_path)
                continue
            if cmd_lower == "/status":
                _print_status(agents)
                continue
            if cmd_lower == "/cost":
                _print_cost(ctx)
                continue
            if cmd_lower == "/cost reset":
                ctx.llm.reset_usage_stats()
                console.print("  [dim]usage stats reset[/dim]")
                continue
            if cmd_lower == "/memory":
                await _print_memory_status(ctx)
                continue
            if cmd_lower == "/memory clear":
                for ag in ctx.agent_map.values():
                    removed = sum(1 for m in ag.memory.short_term if m.role != "system")
                    await ag.memory.clear_short_term()
                    console.print(
                        f"  [green]cleared {ag.emoji} {ag.display_name} "
                        f"({removed} messages)[/green]"
                    )
                continue
            if cmd_lower == "/compact":
                await _init_agent_lazy(agent, ctx)
                result = await agent.compact()
                console.print(f"  [green]✓ {result}[/green]")
                continue
            if cmd_lower == "/history":
                _print_history(ctx)
                continue
            if cmd_lower == "/mcp":
                _print_mcp_status(ctx)
                continue
            if cmd_lower == "/skills":
                _print_skills(agent)
                continue
            if cmd_lower.startswith("/use "):
                skill_name = cmd[5:].strip()
                if agent.activate_skill(skill_name):
                    console.print(f"  [green]+ {skill_name}[/green]")
                else:
                    console.print(
                        f"  [red]unknown skill: {skill_name}[/red] [dim](/skills to list)[/dim]"
                    )
                continue
            if cmd_lower == "/deactivate":
                agent.deactivate_all_skills()
                console.print("  [dim]skills deactivated[/dim]")
                continue
            if cmd_lower == "/workspace":
                _print_workspace(ctx)
                continue
            if cmd_lower.startswith("/workspace set "):
                _handle_workspace_set(cmd, ctx)
                continue
            if cmd_lower == "/workspace auto":
                _handle_workspace_auto(ctx)
                continue
            if cmd_lower == "/sessions":
                await _print_sessions(agent)
                continue
            if cmd_lower.startswith("/session "):
                await _handle_session_command(cmd, agent)
                continue
            if cmd_lower.startswith("/task "):
                goal = cmd[6:].strip()
                if goal:
                    # Lazy-init all agents for orchestration
                    for ag in agents.values():
                        await _init_agent_lazy(ag, ctx)
                    await _run_task(goal, agents)
                continue
            if cmd_lower.startswith("/model"):
                _handle_model_command(cmd, ctx)
                continue
            if cmd_lower.startswith("/apikey"):
                _handle_apikey_command(cmd, ctx)
                continue
            if cmd_lower == "/version":
                console.print(f"  Weather Agents [bold]v{__version__}[/bold]")
                continue
            if cmd_lower.lstrip("/") in AGENT_CLASSES:
                new_name = cmd_lower.lstrip("/")
                new_agent = agents[new_name]
                await _init_agent_lazy(new_agent, ctx)
                current = new_name
                agent = new_agent
                color = AGENT_COLORS.get(new_name, "white")
                switch_msg = Text()
                switch_msg.append("  ")
                switch_msg.append(f"{agent.emoji} ", style=f"bold {color}")
                switch_msg.append(agent.display_name, style=f"bold {color}")
                switch_msg.append("  ready", style="dim")
                console.print(switch_msg)
                continue
            if cmd_lower.startswith("/") and cmd.strip() != "/":
                _print_help()
                continue

            # --- Streaming chat with tool-call support ---
            await _init_agent_lazy(agent, ctx)
            t0 = time.monotonic()
            interrupted = False
            md_content = ""
            status_text = ""
            activities: list[dict] = []

            live = Live(
                _build_stream_display(agent, "", "", activities),
                console=console,
                refresh_per_second=12,
                transient=False,
            )
            live.start()

            try:
                async for event in agent.chat_stream(inp):
                    if event["type"] == "content":
                        md_content += event["text"]
                        live.update(
                            _build_stream_display(agent, status_text, md_content, activities)
                        )
                    elif event["type"] == "tool_status":
                        status_text = event["label"]
                        activities.append({"label": event["label"], "status": "running"})
                        live.update(
                            _build_stream_display(agent, status_text, md_content, activities)
                        )
                    elif event["type"] == "tool_done":
                        status_text = ""
                        for a in activities:
                            if a["label"] == event["label"] and a["status"] == "running":
                                a["status"] = "done" if event.get("success") else "error"
                                break
                        live.update(
                            _build_stream_display(agent, status_text, md_content, activities)
                        )
                    elif event["type"] == "done":
                        break
            except KeyboardInterrupt:
                interrupted = True
            finally:
                if md_content.strip():
                    live.update(
                        _build_response_panel(agent, md_content, time.monotonic() - t0, interrupted)
                    )
                live.stop()

            if not md_content.strip():
                if interrupted:
                    console.print("  [dim]interrupted[/dim]")
                else:
                    console.print("  [dim red]no response received[/dim red]")
                continue

    finally:
        console.print()
        console.print(Rule(style="dim"))
        console.print("  [dim]Session ended[/dim]")
        await ctx.close_all()


# -- Welcome & Help --------------------------------------------------------


def _print_welcome(model: str, workspace_path: str = "") -> None:
    console.print()

    # ── Header panel ───────────────────────────────────────────────────────
    agents_info = [
        ("\U0001f32b", "Fog", "research", AGENT_COLORS["fog"]),
        ("\U0001f327", "Rain", "codegen", AGENT_COLORS["rain"]),
        ("❄", "Frost", "review", AGENT_COLORS["frost"]),
        ("\U0001f328", "Snow", "planning", AGENT_COLORS["snow"]),
        ("\U0001f4a7", "Dew", "devops", AGENT_COLORS["dew"]),
    ]

    # Agent roster table inside welcome panel
    roster = Table(show_header=False, box=None, padding=(0, 2, 0, 0), expand=False)
    roster.add_column(width=4)  # emoji
    roster.add_column(width=8)  # name
    roster.add_column(style="dim", width=12)  # role

    for emoji, name, role, color in agents_info:
        roster.add_row(
            Text(emoji),
            Text(name, style=f"bold {color}"),
            Text(role),
        )

    meta = Text()
    meta.append("\nmodel  ", style="dim")
    meta.append(model, style="cyan")
    if workspace_path:
        meta.append("  ·  ws  ", style="dim")
        short_ws = workspace_path if len(workspace_path) <= 40 else "…" + workspace_path[-38:]
        meta.append(short_ws, style="dim white")

    meta.append("\n\n")
    meta.append("Type ", style="dim")
    meta.append("/", style="cyan bold")
    meta.append(" for commands, ", style="dim")
    meta.append("/help", style="cyan")
    meta.append(" for reference", style="dim")

    content = Table(show_header=False, box=None, padding=0)
    content.add_column()
    content.add_row(roster)
    content.add_row(meta)

    console.print(
        Panel(
            content,
            title="[bold]Weather Agents[/bold]  [dim]v" + __version__ + "[/dim]",
            title_align="left",
            border_style="dim",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()


def _print_help() -> None:
    sections = [
        (
            "Agents",
            [
                ("/fog  /rain  /frost  /snow  /dew", "switch active agent"),
                ("/task <goal>", "multi-agent orchestration"),
            ],
        ),
        (
            "Config",
            [
                ("/model", "view current model"),
                ("/model <name>", "set default model for all agents"),
                ("/model <agent> <name>", "override model per agent"),
                ("/apikey", "list API keys"),
                ("/apikey set <prov> <key>", "add / replace key"),
                ("/apikey del <prov>", "remove key"),
                ("/workspace", "workspace info"),
                ("/workspace set <path>", "set custom workspace"),
                ("/workspace auto", "reset to auto-detect"),
            ],
        ),
        (
            "Skills",
            [
                ("/skills", "list available skills"),
                ("/use <skill>", "activate a skill"),
                ("/deactivate", "deactivate all skills"),
            ],
        ),
        (
            "Info",
            [
                ("/status", "agent overview table"),
                ("/cost", "token usage & cost breakdown"),
                ("/cost reset", "reset usage counters"),
                ("/compact", "compress context window"),
                ("/history", "event log"),
                ("/mcp", "MCP server status"),
                ("/memory", "memory layer stats"),
                ("/memory clear", "clear short-term memory"),
                ("/version", "version info"),
            ],
        ),
        (
            "Session",
            [
                ("/sessions", "list saved sessions"),
                ("/session new [name]", "start a new session"),
                ("/session load <id>", "switch to session"),
                ("/session delete <id>", "delete session"),
                ("/clear", "clear screen & redraw"),
                ("/quit", "exit"),
            ],
        ),
    ]

    console.print()
    for title, items in sections:
        console.print(Rule(f"  {title}  ", align="left", style="dim"))
        tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 2))
        tbl.add_column(width=34, no_wrap=True)
        tbl.add_column(style="dim")
        for cmd, desc in items:
            tbl.add_row(Text(cmd, style="cyan"), desc)
        console.print(tbl)
    console.print()


# -- Display helpers -------------------------------------------------------


def _print_status(agents: dict) -> None:
    console.print()
    console.print(Rule("  Agents  ", align="left", style="dim"))

    tbl = Table(
        show_header=True,
        box=box.SIMPLE_HEAD,
        padding=(0, 2, 0, 0),
        header_style="dim",
        show_edge=False,
    )
    tbl.add_column("Agent", width=18)
    tbl.add_column("State", width=8)
    tbl.add_column("Skills", style="dim", min_width=10)
    tbl.add_column("Calls", justify="right", width=6)
    tbl.add_column("In / Out tokens", justify="right", width=20)

    for a in agents.values():
        s = a.get_status()
        name = s["name"]
        color = AGENT_COLORS.get(name, "white")
        active_skills = [sk["name"] for sk in s.get("skills", []) if sk.get("active")]
        skills_str = ", ".join(active_skills) if active_skills else "—"
        state_color = "green" if s["state"] == "idle" else "yellow"
        tokens = f"{s['usage']['prompt_tokens']:,}  /  {s['usage']['completion_tokens']:,}"
        agent_cell = Text()
        agent_cell.append(f"{s['emoji']} ", style=f"bold {color}")
        agent_cell.append(s["display_name"], style=f"bold {color}")

        tbl.add_row(
            agent_cell,
            Text(s["state"], style=state_color),
            skills_str,
            str(s["usage"]["calls"]),
            Text(tokens, style="dim"),
        )
    console.print(tbl)


def _print_cost(ctx) -> None:
    console.print()
    console.print(Rule("  Usage & Cost  ", align="left", style="dim"))
    stats = ctx.llm.get_usage_stats()
    if not stats:
        console.print("  [dim]no usage recorded yet[/dim]")
        return

    tbl = Table(
        show_header=True,
        box=box.SIMPLE_HEAD,
        padding=(0, 2, 0, 0),
        header_style="dim",
        show_edge=False,
    )
    tbl.add_column("Agent", width=12)
    tbl.add_column("Calls", justify="right", width=6)
    tbl.add_column("In tokens", justify="right", width=12)
    tbl.add_column("Out tokens", justify="right", width=12)
    tbl.add_column("Cost", justify="right", width=10)

    total_cost = 0.0
    for name, s in stats.items():
        cost = s.get("cost", 0.0)
        total_cost += cost
        cost_style = "green" if cost < 0.01 else "yellow" if cost < 0.10 else "red"
        tbl.add_row(
            Text(name, style="cyan"),
            str(s.get("calls", 0)),
            f"{s.get('prompt_tokens', 0):,}",
            f"{s.get('completion_tokens', 0):,}",
            Text(f"${cost:.4f}", style=cost_style),
        )

    # Total row
    total_style = "green" if total_cost < 0.05 else "yellow" if total_cost < 0.50 else "red"
    tbl.add_row(
        Text("total", style="bold"),
        "",
        "",
        "",
        Text(f"${total_cost:.4f}", style=f"bold {total_style}"),
    )
    console.print(tbl)


def _print_history(ctx) -> None:
    events = ctx.bus.get_history(limit=20)
    if not events:
        console.print("  [dim]no events yet[/dim]")
        return

    console.print()
    console.print(Rule("  Event Log  ", align="left", style="dim"))

    tbl = Table(
        show_header=True,
        box=box.SIMPLE_HEAD,
        padding=(0, 2, 0, 0),
        header_style="dim",
        show_edge=False,
    )
    tbl.add_column("Time", width=10, style="dim")
    tbl.add_column("Type", width=18)
    tbl.add_column("Source", width=8)
    tbl.add_column("Detail", style="dim")

    for e in events[-15:]:
        ts = e.timestamp.strftime("%H:%M:%S")
        summary = _summarize_event(e.type.value, e.data)
        tbl.add_row(
            ts,
            Text(e.type.value, style="cyan"),
            Text(e.source, style="bold"),
            summary,
        )
    console.print(tbl)


def _summarize_event(event_type: str, data: dict | None) -> str:
    """Render an event's data field as a short, readable line — no truncated dicts."""
    if not data:
        return ""
    if event_type == "tool_call":
        tool = data.get("tool", "?")
        args = data.get("args", {})
        arg_bits = [f"{k}={_short(v)}" for k, v in list(args.items())[:2]]
        suffix = f"({', '.join(arg_bits)})" if arg_bits else ""
        return f"{tool}{suffix}"
    if event_type == "llm_call":
        usage = data.get("usage") or {}
        ptok = usage.get("prompt_tokens", 0)
        ctok = usage.get("completion_tokens", 0)
        return f"{data.get('model', '?')}  {ptok}→{ctok} tok"
    if event_type == "state_change":
        return f"{data.get('old_state', '?')} → {data.get('new_state', '?')}"
    # Fallback: comma-separated key=value pairs trimmed to width
    pairs = [f"{k}={_short(v)}" for k, v in list(data.items())[:3]]
    return ", ".join(pairs)


def _short(v) -> str:
    s = str(v)
    return s if len(s) <= 30 else s[:27] + "..."


def _print_mcp_status(ctx) -> None:
    mcp_servers = ctx.config.mcp.servers
    if not mcp_servers:
        console.print("  [dim]no MCP servers configured[/dim]")
        return

    connected: dict[str, str] = {}
    for line in ctx.mcp_status or []:
        if ":" in line:
            name, info = line.split(":", 1)
            connected[name.strip()] = info.strip()

    console.print()
    console.print(Rule("  MCP Servers  ", align="left", style="dim"))

    tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column(width=3)  # icon
    tbl.add_column(width=20)  # name
    tbl.add_column(width=6, style="dim")  # transport
    tbl.add_column()  # status

    for s in mcp_servers:
        name = s.get("name", "?")
        transport = "stdio" if s.get("command") else "sse"
        enabled = s.get("enabled", True)
        if not enabled:
            icon = Text("○", style="dim")
            status = Text("disabled", style="dim")
        elif name in connected:
            icon = Text("●", style="green")
            status = Text(connected[name], style="green")
        else:
            icon = Text("●", style="yellow")
            status = Text("not connected", style="yellow")
        tbl.add_row(icon, Text(name, style="cyan"), transport, status)

    console.print(tbl)


async def _print_memory_status(ctx) -> None:
    console.print()
    console.print(Rule("  Memory  ", align="left", style="dim"))

    tbl = Table(
        show_header=True,
        box=box.SIMPLE_HEAD,
        padding=(0, 2, 0, 0),
        header_style="dim",
        show_edge=False,
    )
    tbl.add_column("Agent", width=18)
    tbl.add_column("Short", justify="right", width=8)
    tbl.add_column("Working", justify="right", width=8)
    tbl.add_column("Long-term", justify="right", width=10)

    for ag in ctx.agent_map.values():
        color = AGENT_COLORS.get(ag.name, "white")
        short = len(ag.memory.short_term)
        working = len(ag.memory.working)
        long_term = await ag.memory.recall(limit=100)

        agent_cell = Text()
        agent_cell.append(f"{ag.emoji} ", style=f"bold {color}")
        agent_cell.append(ag.display_name, style=f"bold {color}")

        tbl.add_row(
            agent_cell,
            Text(str(short), style="dim" if short == 0 else "default"),
            Text(str(working), style="dim" if working == 0 else "default"),
            Text(str(len(long_term)), style="dim" if not long_term else "default"),
        )
    console.print(tbl)


async def _print_sessions(agent) -> None:
    sessions = await agent.memory.list_sessions()
    active_id = agent.memory.get_active_session()

    console.print()
    console.print(Rule("  Sessions  ", align="left", style="dim"))

    if not sessions:
        console.print("  [dim]no saved sessions[/dim]")
        console.print("  [dim]/session new [name]  — start a new one[/dim]")
        return

    tbl = Table(
        show_header=True,
        box=box.SIMPLE_HEAD,
        padding=(0, 2, 0, 0),
        header_style="dim",
        show_edge=False,
    )
    tbl.add_column("", width=2)  # active marker
    tbl.add_column("ID", width=20, style="cyan")
    tbl.add_column("Name / Preview", min_width=24)
    tbl.add_column("Msgs", justify="right", width=6, style="dim")

    for s in sessions:
        active = s["id"] == active_id
        marker = Text("●", style="green") if active else Text(" ")
        sid = s["id"]
        name = s["name"] or s["preview"] or "(empty)"
        if len(name) > 48:
            name = name[:45] + "…"
        count = s["message_count"]
        tbl.add_row(marker, sid, Text(name, style="bold" if active else "default"), str(count))

    console.print(tbl)
    console.print()
    console.print("  [dim]/session new [name]    start fresh session[/dim]")
    console.print("  [dim]/session load <id>     switch to session[/dim]")
    console.print("  [dim]/session delete <id>   delete session[/dim]")


async def _handle_session_command(cmd: str, agent) -> None:
    parts = cmd.strip().split(maxsplit=2)
    if len(parts) < 2:
        await _print_sessions(agent)
        return

    action = parts[1].lower()

    if action == "new":
        name = parts[2] if len(parts) > 2 else None
        sid = await agent.memory.create_session(name)
        console.print(f"  [green]+ new session [cyan]{sid}[/cyan][/green]")
        return

    if action == "load":
        if len(parts) < 3:
            console.print("  [red]usage: /session load <id>[/red]")
            return
        sid = parts[2]
        ok = await agent.memory.load_session(sid)
        if ok:
            console.print(f"  [green]loaded session [cyan]{sid}[/cyan][/green]")
        else:
            console.print(f"  [red]session not found: {sid}[/red]")
        return

    if action in ("delete", "del", "rm"):
        if len(parts) < 3:
            console.print("  [red]usage: /session delete <id>[/red]")
            return
        sid = parts[2]
        ok = await agent.memory.delete_session(sid)
        if ok:
            console.print(f"  [green]deleted session [cyan]{sid}[/cyan][/green]")
        else:
            console.print(f"  [red]session not found: {sid}[/red]")
        return

    console.print("  [red]usage: /session [new|load|delete] ...[/red]")


def _print_skills(agent) -> None:
    skills = agent.get_available_skills()
    color = AGENT_COLORS.get(agent.name, "white")

    console.print()
    console.print(
        Rule(
            f"  {agent.emoji} {agent.display_name} Skills  ",
            align="left",
            style="dim",
        )
    )

    if not skills:
        console.print("  [dim]no skills available[/dim]")
        return

    tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column(width=3)  # status dot
    tbl.add_column(width=22)  # name
    tbl.add_column(style="dim")  # description

    for sk in skills:
        dot = Text("●", style=f"bold {color}") if sk["active"] else Text("○", style="dim")
        tbl.add_row(
            dot, Text(sk["name"], style="cyan" if sk["active"] else "dim"), sk["description"]
        )

    console.print(tbl)
    console.print()
    console.print("  [dim]/use <skill>   activate  ·  /deactivate   deactivate all[/dim]")


# -- Workspace management ----------------------------------------------------


def _print_workspace_path() -> None:
    """Print workspace path, disk info, and subdirectories."""
    import shutil

    cfg = load_config()
    configured = cfg.workspace.path
    is_auto = configured.lower() == "auto"
    resolved = resolve_workspace_path(configured)
    resolved_str = str(resolved.resolve())
    exists = resolved.exists()

    console.print()
    console.print(Rule("  Workspace  ", align="left", style="dim"))

    # Key-value info table
    kv = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    kv.add_column(width=12, style="dim")
    kv.add_column()

    kv.add_row("mode", Text("auto", style="cyan") if is_auto else Text("custom", style="yellow"))
    if is_auto:
        kv.add_row("detected", str(detect_best_workspace_root()))
    else:
        kv.add_row("configured", configured)
    kv.add_row("resolved", resolved_str)
    kv.add_row(
        "status", Text("exists", style="green") if exists else Text("not created", style="yellow")
    )

    try:
        usage = shutil.disk_usage(resolved_str)
        ratio = usage.free / usage.total if usage.total else 0
        bar_filled = max(1, int(ratio * 10))
        disk_bar_color = "green" if ratio > 0.2 else "yellow" if ratio > 0.1 else "red"
        disk_bar = Text("█" * bar_filled + "─" * (10 - bar_filled), style=disk_bar_color)
        disk_info = Text()
        disk_info.append(format_bytes(usage.free), style="green")
        disk_info.append(f" free / {format_bytes(usage.total)}  ")
        disk_info.append(disk_bar)
        kv.add_row("disk", disk_info)
    except OSError:
        kv.add_row("disk", Text("unavailable", style="red"))

    console.print(kv)

    # Subdirectory listing
    if exists:
        subs = sorted(resolved.iterdir())
        if subs:
            console.print()
            console.print("  [dim]contents[/dim]")
            for child in subs:
                if child.is_dir():
                    console.print(f"    [dim]{child.name}/[/dim]")
                else:
                    console.print(f"    [dim]{child.name}[/dim]")

    # Hint
    console.print()
    console.print("  [dim]/workspace set <path>   set custom path[/dim]")
    if not is_auto:
        console.print("  [dim]/workspace auto         reset to auto-detect[/dim]")

    # Windows drive list
    if os.name == "nt":
        console.print()
        console.print(Rule("  Drives  ", align="left", style="dim"))
        from weather_agents.core.workspace import _get_drive_list

        drive_tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        drive_tbl.add_column(width=2)  # active marker
        drive_tbl.add_column(width=6)  # path
        drive_tbl.add_column(width=12)  # free
        drive_tbl.add_column(width=12, style="dim")  # total
        drive_tbl.add_column()  # bar

        for d in _get_drive_list():
            active = str(resolved).startswith(d.path)
            marker = Text("●", style="green") if active else Text(" ")
            ratio = d.free_bytes / d.total_bytes if d.total_bytes else 0
            bar_c = "green" if ratio > 0.2 else "yellow" if ratio > 0.1 else "red"
            filled = max(1, int(ratio * 10))
            bar = Text("█" * filled + "─" * (10 - filled), style=bar_c)
            drive_tbl.add_row(
                marker,
                Text(d.path, style="cyan"),
                Text(f"{format_bytes(d.free_bytes)} free", style="green"),
                f"/ {format_bytes(d.total_bytes)}",
                bar,
            )
        console.print(drive_tbl)


def _free_bar(free: int, total: int) -> str:
    """Draw a minimal 10-char usage bar."""
    if total <= 0:
        return "[dim][----------][/dim]"
    ratio = free / total
    filled = max(1, int(ratio * 10))
    bar = "█" * filled + "─" * (10 - filled)
    color = "green" if ratio > 0.2 else "yellow" if ratio > 0.1 else "red"
    return f"[{color}]{bar}[/{color}]"


def _print_workspace(ctx) -> None:
    _print_workspace_path()


def _handle_workspace_set(cmd: str, ctx) -> None:
    path_str = cmd[len("/workspace set ") :].strip()
    if not path_str:
        console.print("  [red]usage: /workspace set <absolute-path>[/red]")
        return

    # Resolve and validate
    from pathlib import Path

    resolved = Path(os.path.expanduser(path_str)).resolve()
    if not resolved.is_absolute():
        console.print("  [red]path must be absolute[/red]")
        return

    ok, msg = set_config("workspace.path", str(resolved))
    color = "green" if ok else "red"
    console.print(f"  [{color}]{msg}[/{color}]")

    if ok:
        # Immediately create the new workspace
        try:
            init_workspace(resolved)
            console.print(f"  [green]workspace created at {resolved}[/green]")
        except OSError as e:
            console.print(f"  [yellow]Warning: could not create workspace: {e}[/yellow]")


def _handle_workspace_auto(ctx) -> None:
    ok, msg = delete_config("workspace.path")
    color = "green" if ok else "red"
    console.print(f"  [{color}]{msg}[/{color}]")

    # Detect and create the auto workspace for display
    root = detect_best_workspace_root()
    try:
        init_workspace(root)
        console.print(f"  [green]workspace -> {root}[/green]")
    except OSError as e:
        console.print(f"  [yellow]Warning: {e}[/yellow]")


# -- Model & API key management --------------------------------------------


def _handle_model_command(cmd: str, ctx) -> None:
    parts = cmd.strip().split(maxsplit=1)
    if len(parts) == 1:
        current = ctx.config.llm.default_model
        console.print(f"\n  [bold]default:[/bold] [cyan]{current}[/cyan]\n")
        for name in AGENT_CLASSES:
            agent_cfg = getattr(ctx.config.agents, name, None)
            m = agent_cfg.model if agent_cfg and agent_cfg.model else current
            marker = "" if agent_cfg and agent_cfg.model else " [dim](default)[/dim]"
            console.print(f"  {AGENT_EMOJI[name]} {name:<6}  {m}{marker}")
        console.print(
            "\n  [dim]/model <name>           set default model\n"
            "  /model <agent> <name>    set agent model\n"
            "  /model <agent> default   reset to default[/dim]"
        )
        return

    arg = parts[1].strip()
    tokens = arg.split(maxsplit=1)

    # Guard: a single token that happens to be an agent name like "/model fog"
    # used to silently get persisted as the default model. Require explicit form.
    if len(tokens) == 1 and tokens[0] in AGENT_CLASSES:
        console.print(f"  [red]missing model name. Usage: /model {tokens[0]} <model>[/red]")
        return

    if len(tokens) == 2 and tokens[0] in AGENT_CLASSES:
        agent_name, model_name = tokens
        if model_name.lower() == "default":
            delete_config(f"model.{agent_name}")
            agent_cfg = getattr(ctx.config.agents, agent_name)
            agent_cfg.model = ""
            console.print(f"  [green]{AGENT_EMOJI[agent_name]} {agent_name} -> default[/green]")
        else:
            set_config(f"model.{agent_name}", model_name)
            agent_cfg = getattr(ctx.config.agents, agent_name)
            agent_cfg.model = model_name
            console.print(
                f"  [green]{AGENT_EMOJI[agent_name]} {agent_name} -> {model_name}[/green]"
            )
        return

    model_name = arg
    ok, msg = set_config("default_model", model_name)
    if ok:
        ctx.config.llm.default_model = model_name
        console.print(f"  [green]model -> {model_name}[/green]")
    else:
        console.print(f"  [red]{msg}[/red]")


def _handle_apikey_command(cmd: str, ctx) -> None:
    parts = cmd.strip().split(maxsplit=2)

    if len(parts) == 1:
        keys = ctx.config.llm.api_keys
        if not keys:
            console.print("  [dim]no API keys configured[/dim]")
        else:
            console.print()
            for provider, key in keys.items():
                masked = key[:8] + "****" + key[-4:] if len(key) > 16 else key[:4] + "****"
                console.print(
                    f"  [green]●[/green]  [cyan]{provider:<12}[/cyan]  [dim]{masked}[/dim]"
                )
        console.print(
            "\n  [dim]/apikey set <provider> <key>    add or replace\n"
            "  /apikey del <provider>             remove[/dim]"
        )
        return

    action = parts[1].lower()

    if action in ("set", "add") and len(parts) == 3:
        tokens = parts[2].strip().split(maxsplit=1)
        if len(tokens) != 2:
            console.print("  [red]usage: /apikey set <provider> <key>[/red]")
            return
        provider, key = tokens
        provider = provider.lower()
        ok, msg = set_config(f"api_key.{provider}", key)
        if ok:
            ctx.config.llm.api_keys[provider] = key
            _sync_api_keys_to_env({provider: key})
            console.print(f"  [green]+ {provider} key saved[/green]")
        else:
            console.print(f"  [red]{msg}[/red]")
        return

    if action in ("del", "delete", "rm", "remove"):
        if len(parts) < 3:
            console.print("  [red]usage: /apikey del <provider>[/red]")
            return
        provider = parts[2].strip().lower()
        ok, msg = delete_config(f"api_key.{provider}")
        if ok:
            ctx.config.llm.api_keys.pop(provider, None)
            from weather_agents.core.config import _ENV_KEY_MAP

            env_var = _ENV_KEY_MAP.get(provider, f"{provider.upper()}_API_KEY")
            os.environ.pop(env_var, None)
            console.print(f"  [green]- {provider} key removed[/green]")
        else:
            console.print(f"  [red]{msg}[/red]")
        return

    console.print("  [red]usage: /apikey [set <provider> <key> | del <provider>][/red]")


# -- Task orchestration ----------------------------------------------------


async def _run_task(goal: str, agents=None) -> None:
    own_ctx = None
    if agents is None:
        own_ctx = create_system_context()
        await own_ctx.init_all()
        agents = own_ctx.agent_map

    emoji_map = AGENT_EMOJI

    try:
        from weather_agents.core.factory import orchestrate_task

        status_handles: dict[str, Any] = {}

        async def _on_start(t):
            emoji = emoji_map.get(t.assigned_to or "", "?")
            sh = console.status(f"[dim]{emoji} {t.description}...[/dim]", spinner="dots")
            sh.start()
            status_handles[t.id] = sh

        async def _on_done(t, r):
            sh = status_handles.pop(t.id, None)
            if sh:
                sh.stop()
            emoji = emoji_map.get(r.agent, "?")
            icon = "[green]✓[/green]" if r.success else "[red]✗[/red]"
            console.print(f"  {icon} {emoji} {r.description}")

        console.print()
        console.print(Rule("  Task  ", align="left", style="dim"))
        console.print(f"  [bold]{goal}[/bold]")
        console.print()

        with console.status("  [dim]planning…[/dim]", spinner="dots"):
            tasks, results, summary = await orchestrate_task(
                goal,
                agents,
                on_task_start=_on_start,
                on_task_done=_on_done,
                result_truncate=500,
            )

        if not tasks:
            console.print("  [dim]no tasks generated[/dim]")
            return

        # Task plan
        console.print(Rule("  Plan  ", align="left", style="dim"))
        plan_tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        plan_tbl.add_column(width=4, style="dim")  # id
        plan_tbl.add_column(width=4)  # emoji
        plan_tbl.add_column()  # description
        plan_tbl.add_column(width=12, style="dim")  # dep

        for t in tasks:
            emoji = emoji_map.get(t.assigned_to or "", "?")
            dep = f"← {t.parent_id}" if t.parent_id else ""
            plan_tbl.add_row(f"{t.id}.", emoji, t.description, dep)
        console.print(plan_tbl)

        # Results
        console.print()
        ok = sum(1 for r in results if r.success)
        total = len(results)
        result_color = "green" if ok == total else "yellow" if ok > 0 else "red"
        console.print(
            f"  [{result_color}]{'✓' if ok == total else '!'} {ok}/{total} tasks completed[/{result_color}]"
        )

        if summary:
            console.print()
            console.print(Rule("  Summary  ", align="left", style="dim"))
            console.print(Padding(Markdown(summary), pad=(0, 2, 0, 2)))

    finally:
        # Clean up any lingering status handles
        for sh in status_handles.values():
            sh.stop()
        if own_ctx:
            await own_ctx.close_all()


# -- CLI commands ----------------------------------------------------------


@app.command()
def chat(
    agent: str = typer.Argument("fog", help="Agent name (fog/rain/frost/snow/dew)"),
    message: str = typer.Argument(None, help="Message (omit for interactive mode)"),
) -> None:
    """Chat with an agent. Omit message for interactive mode."""
    if agent not in AGENT_CLASSES:
        console.print(f"[red]Unknown agent: {agent}. Use: {', '.join(AGENT_CLASSES)}[/red]")
        raise typer.Exit(1)

    # First-run: nothing is configured yet. Walk the user through the wizard,
    # then drop straight into chat — no separate `wa init` step required.
    if not _is_configured():
        console.print("\n  [yellow]No API key configured yet — running first-run setup.[/yellow]")
        _run_setup_wizard()
        if not _is_configured():
            console.print(
                "\n  [yellow]Skipped without entering a key. "
                "Run [cyan]wa init[/cyan] later when ready.[/yellow]\n"
            )
            raise typer.Exit(0)

    if message:
        asyncio.run(_chat_single(agent, message))
    else:
        asyncio.run(_interactive(agent))


@app.command()
def task(goal: str = typer.Argument(..., help="Task goal for multi-agent orchestration")) -> None:
    """Multi-agent orchestration: Snow decomposes and coordinates agents."""
    asyncio.run(_run_task(goal))


@app.command()
def status() -> None:
    """Show all agent status and model configuration."""
    ctx = create_system_context()
    console.print()
    console.print(Rule("  Agent Configuration  ", align="left", style="dim"))

    tbl = Table(
        show_header=True,
        box=box.SIMPLE_HEAD,
        padding=(0, 2, 0, 0),
        header_style="dim",
        show_edge=False,
    )
    tbl.add_column("Agent", width=20)
    tbl.add_column("Specialty", style="dim", width=14)
    tbl.add_column("Model", width=30)
    tbl.add_column("Skills", style="dim")

    for name, cls in AGENT_CLASSES.items():
        color = AGENT_COLORS.get(name, "white")
        model = getattr(ctx.config.agents, name).model or ctx.config.llm.default_model
        skills = ", ".join(cls.skill_names) if cls.skill_names else "—"
        agent_cell = Text()
        agent_cell.append(f"{AGENT_EMOJI[name]} ", style=f"bold {color}")
        agent_cell.append(cls.display_name, style=f"bold {color}")
        tbl.add_row(agent_cell, cls.specialty, Text(model, style="cyan"), skills)
    console.print(tbl)


# -- Config ----------------------------------------------------------------


@app.command()
def config(
    action: str = typer.Argument("list", help="list / set / delete / models"),
    key: str = typer.Argument(None, help="Config key"),
    value: str = typer.Argument(None, help="Config value (for set)"),
) -> None:
    """Manage configuration."""
    if action == "list":
        cfg = load_config()
        console.print()
        console.print(Rule("  Configuration  ", align="left", style="dim"))

        kv = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        kv.add_column(width=14, style="dim")
        kv.add_column()
        kv.add_row("default model", Text(cfg.llm.default_model, style="cyan"))
        kv.add_row("temperature", str(cfg.llm.temperature))
        kv.add_row("max tokens", str(cfg.llm.max_tokens))
        kv.add_row("timeout", f"{cfg.llm.timeout}s")
        console.print(kv)

        console.print()
        console.print(Rule("  Per-agent Models  ", align="left", style="dim"))
        agent_tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        agent_tbl.add_column(width=20)
        agent_tbl.add_column()
        for name in AGENT_CLASSES:
            color = AGENT_COLORS.get(name, "white")
            attr = getattr(cfg.agents, name)
            m = attr.model or ""
            model_cell = Text(m, style="cyan") if m else Text("(default)", style="dim")
            agent_cell = Text()
            agent_cell.append(f"{AGENT_EMOJI[name]} ", style=f"bold {color}")
            agent_cell.append(name, style=f"bold {color}")
            agent_tbl.add_row(agent_cell, model_cell)
        console.print(agent_tbl)

        if cfg.llm.api_keys:
            console.print()
            console.print(Rule("  API Keys  ", align="left", style="dim"))
            key_tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
            key_tbl.add_column(width=3)
            key_tbl.add_column(width=14)
            key_tbl.add_column(style="dim")
            for p, v in cfg.llm.api_keys.items():
                masked = v[:8] + "…" + v[-4:] if len(v) > 16 else v[:4] + "…"
                key_tbl.add_row(Text("●", style="green"), Text(p, style="cyan"), masked)
            console.print(key_tbl)

        console.print()
        console.print(f"  [dim]{USER_CONFIG_DIR / 'config.yaml'}[/dim]")

    elif action == "set":
        if not key or value is None:
            console.print("  [red]usage: wa config set <key> <value>[/red]")
            raise typer.Exit(1)
        ok, msg = set_config(key, value)
        color = "green" if ok else "red"
        console.print(f"  [{color}]{msg}[/{color}]")

    elif action == "delete":
        if not key:
            console.print("  [red]usage: wa config delete <key>[/red]")
            raise typer.Exit(1)
        ok, msg = delete_config(key)
        color = "green" if ok else "red"
        console.print(f"  [{color}]{msg}[/{color}]")

    elif action == "models":
        catalog = load_model_catalog()
        if not catalog:
            console.print("  [yellow]no models.yaml found[/yellow]")
            return
        console.print(format_models_for_display(catalog))

    else:
        console.print(f"  [red]unknown action: {action} (list / set / delete / models)[/red]")


# -- Memory ----------------------------------------------------------------


@app.command()
def memory(
    action: str = typer.Argument("status", help="status / clear"),
    agent_name: str = typer.Argument(None, help="Agent name or omit for all"),
) -> None:
    """Manage agent memory."""

    async def _run() -> None:
        ctx = create_system_context()
        await ctx.init_all()
        try:
            if action == "clear":
                targets = [agent_name] if agent_name else list(ctx.agent_map.keys())
                for name in targets:
                    agent = ctx.agent_map.get(name)
                    if not agent:
                        console.print(f"  [red]unknown agent: {name}[/red]")
                        continue
                    # Count only non-system messages — those are what clear_short_term removes.
                    removed = sum(1 for m in agent.memory.short_term if m.role != "system")
                    await agent.memory.clear_short_term()
                    console.print(
                        f"  [green]cleared {agent.emoji} {agent.display_name} "
                        f"({removed} messages)[/green]"
                    )
            else:
                for _name, agent in ctx.agent_map.items():
                    short = len(agent.memory.short_term)
                    working = len(agent.memory.working)
                    long_term = await agent.memory.recall(limit=100)
                    console.print(
                        f"  {agent.emoji} {agent.display_name}  "
                        f"[dim]{short} short / {working} working / "
                        f"{len(long_term)} long-term[/dim]"
                    )
        finally:
            await ctx.close_all()

    asyncio.run(_run())


# -- Init / Setup Wizard --------------------------------------------------


def _is_configured() -> bool:
    """Has the user supplied at least one API key (config file or env)?"""
    cfg = load_config()
    if any(v for v in cfg.llm.api_keys.values()):
        return True
    for env_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        if os.environ.get(env_var):
            return True
    return False


def _provider_for_model(model: str) -> str:
    """Infer the provider responsible for a model id."""
    m = model.lower()
    if m.startswith("ollama/"):
        return "local"
    if "deepseek" in m:
        return "deepseek"
    if m.startswith(("claude", "anthropic/")):
        return "anthropic"
    if m.startswith(("gpt", "openai/", "o1", "o3", "o4")):
        return "openai"
    return "openai"


def _flatten_catalog(catalog: dict) -> list[tuple[str, str]]:
    """Return [(provider, model_name), ...] preserving provider order."""
    out = []
    for prov, models in catalog.items():
        for m in models:
            out.append((prov, m["name"]))
    return out


def _print_catalog(flat: list[tuple[str, str]]) -> None:
    """Print numbered model menu grouped by provider."""
    last_prov = None
    for i, (prov, name) in enumerate(flat, 1):
        if prov != last_prov:
            console.print(f"\n    [bold dim]{prov.upper()}[/bold dim]")
            last_prov = prov
        console.print(f"      [dim]{i:>2}.[/dim] [cyan]{name}[/cyan]")


def _pick_from_catalog(
    flat: list[tuple[str, str]],
    prompt: str,
    default_idx: int | None = None,
) -> tuple[str, str] | None:
    """Loop until the user types a valid number or hits Enter for default."""
    hint = f" [dim](Enter for {default_idx})[/dim]" if default_idx else ""
    while True:
        raw = console.input(f"  {prompt}{hint}: ").strip()
        if not raw and default_idx is not None:
            return flat[default_idx - 1]
        if raw.isdigit() and 1 <= int(raw) <= len(flat):
            return flat[int(raw) - 1]
        console.print(f"    [red]pick a number 1-{len(flat)}[/red]")


def _collect_keys(providers: set[str]) -> None:
    """Prompt for one API key per cloud provider in the set."""
    cloud = sorted(p for p in providers if p != "local")
    if not cloud:
        console.print("  [dim]All chosen models run locally — no API keys needed.[/dim]")
        return
    console.print(f"\n  [bold]API keys for:[/bold] [cyan]{', '.join(cloud)}[/cyan]")
    console.print("  [dim](pasted keys are hidden in transit but stored in plain YAML)[/dim]\n")
    for provider in cloud:
        cfg = load_config()
        current = cfg.llm.api_keys.get(provider, "")
        suffix = " [dim](Enter to keep current)[/dim]" if current else ""
        key = console.input(f"  {provider:<10} key{suffix}: ").strip()
        if key:
            ok, msg = set_config(f"api_key.{provider}", key)
            color = "green" if ok else "red"
            console.print(f"    [{color}]{msg}[/{color}]")


def _run_setup_wizard() -> None:
    """Walk the user through choosing a model strategy and storing API keys.

    Does NOT enter chat — the caller decides whether to launch _interactive().
    """
    console.print()
    console.print(
        Panel(
            "[bold]Weather Agents Setup[/bold]\n[dim]Configure your agents in 3 steps[/dim]",
            border_style="dim cyan",
            box=box.ROUNDED,
            padding=(1, 2),
            width=44,
        )
    )

    catalog = load_model_catalog()
    if not catalog:
        console.print("\n  [red]No model catalog found. Reinstall and try again.[/red]")
        return
    flat = _flatten_catalog(catalog)

    # Step 1: choose mode
    console.print()
    console.print(Rule("  Step 1 — Agent mode  ", align="left", style="dim"))
    step1_tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    step1_tbl.add_column(width=3, style="cyan bold")
    step1_tbl.add_column(width=12, style="bold")
    step1_tbl.add_column(style="dim")
    step1_tbl.add_row("1.", "Unified", "one model + one API key for all agents  (recommended)")
    step1_tbl.add_row("2.", "Per-agent", "a different model for each agent  (advanced)")
    console.print(step1_tbl)

    mode = ""
    while mode not in ("1", "2"):
        mode = console.input("\n  Choice [1/2] — Enter for 1: ").strip() or "1"
        if mode not in ("1", "2"):
            console.print("  [red]please enter 1 or 2[/red]")

    # Step 2: pick models
    providers_needed: set[str] = set()
    console.print()
    console.print(Rule("  Step 2 — Model selection  ", align="left", style="dim"))

    if mode == "1":
        _print_catalog(flat)
        default_idx = next((i + 1 for i, (p, _) in enumerate(flat) if p == "deepseek"), 1)
        picked = _pick_from_catalog(flat, "\n  Model #", default_idx=default_idx)
        if not picked:
            return
        provider, model_name = picked
        set_config("default_model", model_name)
        for ag in AGENT_CLASSES:
            delete_config(f"model.{ag}")
        console.print(f"  [green]✓ default → {model_name}[/green]")
        providers_needed.add(provider)
    else:
        _print_catalog(flat)
        console.print()
        default_idx = next((i + 1 for i, (p, _) in enumerate(flat) if p == "deepseek"), 1)
        for agent_name, cls in AGENT_CLASSES.items():
            label = f"{AGENT_EMOJI[agent_name]} {cls.display_name} model #"
            picked = _pick_from_catalog(flat, label, default_idx=default_idx)
            if not picked:
                continue
            prov, model_name = picked
            set_config(f"model.{agent_name}", model_name)
            providers_needed.add(prov)
            console.print(
                f"  [green]✓ {AGENT_EMOJI[agent_name]} {agent_name} → {model_name}[/green]"
            )

    # Step 3: collect API keys
    console.print()
    console.print(Rule("  Step 3 — API keys  ", align="left", style="dim"))
    _collect_keys(providers_needed)

    console.print()
    console.print("  [green]✓ Setup complete[/green]")
    cfg_path = USER_CONFIG_DIR / "config.yaml"
    console.print(f"  [dim]config saved to {cfg_path}[/dim]")


@app.command()
def init() -> None:
    """Run the setup wizard, then optionally drop into chat."""
    _run_setup_wizard()
    answer = console.input("  Enter chat now? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        asyncio.run(_interactive())
    else:
        console.print("\n  [dim]Run `wa chat` when ready.[/dim]\n")


# -- Version ---------------------------------------------------------------


@app.command()
def version() -> None:
    """Show version information."""
    console.print(f"  Weather Agents [bold]v{__version__}[/bold]")
