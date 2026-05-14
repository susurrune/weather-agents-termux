"""CLI interface for Weather Agents — terminal agent product."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import typer
from rich.console import Console

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
    """Build the Live renderable: status bar + content + optional activity panel."""
    color = AGENT_COLORS.get(agent.name, "white")
    spin = _next_spin()
    header = f"  {spin}  {agent.emoji}  [bold {color}]{agent.display_name}[/bold {color}]  [dim]{status_text or 'thinking...'}[/dim]"

    if not activities:
        tbl = Table(show_header=False, box=None, padding=0, expand=True)
        tbl.add_column(ratio=1)
        tbl.add_row(Text(header))
        tbl.add_row(Text("─" * min(console.width, 100), style="dim"))
        if md_content:
            tbl.add_row(Markdown(md_content))
        return tbl

    # Two-column: left = content, right = activity log
    tbl = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    tbl.add_column(ratio=7)
    tbl.add_column(ratio=3)

    inner = Table(show_header=False, box=None, padding=0)
    inner.add_column(ratio=1)
    inner.add_row(Text(header))
    inner.add_row(Text("─" * 40, style="dim"))
    if md_content:
        inner.add_row(Markdown(md_content))

    act = Table(show_header=False, box=None, padding=(0, 0), expand=True)
    act.add_column(style="dim")
    for a in activities[-12:]:
        s = a["status"]
        icon = (
            "[green]✓[/green]"
            if s == "done"
            else "[red]✗[/red]"
            if s == "error"
            else "[yellow]●[/yellow]"
        )
        act.add_row(f"{icon}  [dim]{a['label']}[/dim]")
    if not [a for a in activities if a["status"] not in ("done", "error")]:
        act.add_row("")
        act.add_row("[dim]  — idle —[/dim]")

    right = Panel(act, title="Activity", border_style="dim", padding=(0, 1))

    tbl.add_row(inner, right)
    return tbl


def _build_response_panel(
    agent,
    content: str,
    elapsed: float,
    interrupted: bool = False,
) -> Panel:
    """Build a Panel for the final agent response."""
    color = AGENT_COLORS.get(agent.name, "white")
    title = f"{agent.emoji}  {agent.display_name}"
    subtitle = f"{elapsed:.1f}s"
    if interrupted:
        subtitle += " (interrupted)"
    return Panel(
        Markdown(content),
        title=title,
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        border_style=color,
        padding=(1, 2),
    )


def _build_status_line(agent, ctx) -> str:
    """Build a compact status line: model, context bar, msg count, cost."""
    try:
        cu = agent.context_usage()
        model = cu["model"]
        pct = cu["pct"]
        est = cu["estimated_tokens"]
        max_ctx = cu["max_tokens"]
        msgs = cu["message_count"]
        cost = ctx.llm.get_total_cost()

        # 10-char usage bar
        filled = min(10, max(0, int(pct / 10)))
        bar_color = "green" if pct < 50 else "yellow" if pct < 75 else "red"
        bar = f"[{bar_color}]{'█' * filled}{'░' * (10 - filled)}[/{bar_color}]"

        ctx_str = f"{est // 1000}k/{max_ctx // 1000}k" if est > 1000 else f"{est}/{max_ctx}"
        return (
            f"  {bar} [dim]{pct}% {ctx_str}[/dim]  │  "
            f"[dim]{msgs} msgs[/dim]  │  "
            f"[dim]${cost:.4f}[/dim]  │  "
            f"[dim]{model}[/dim]"
        )
    except Exception:
        return ""


# -- Chat -------------------------------------------------------------------


async def _chat_single(agent_name: str, message: str) -> None:
    ctx = create_system_context()
    await ctx.init_all()
    try:
        agent = ctx.agent_map.get(agent_name)
        if not agent:
            console.print(f"[red]Unknown agent: {agent_name}[/red]")
            return
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


async def _interactive(agent_name: str | None = None) -> None:
    ctx = create_system_context()
    await ctx.init_all()

    try:
        agents = ctx.agent_map
        current = agent_name or "fog"
        agent = agents[current]
        model = ctx.config.llm.default_model
        ws = getattr(ctx, "workspace_path", "")
        workspace_path = ws if isinstance(ws, str) else ""
        _print_welcome(model, workspace_path)

        while True:
            console.print()
            # Status bar: context usage + model + cost
            status = _build_status_line(agent, ctx)
            if status:
                console.print(Text(status))
                console.print(Text("  " + "─" * min(console.width - 2, 98), style="dim"))
            else:
                console.print(Text("  " + "─" * min(console.width - 2, 98), style="dim"))
            try:
                color = AGENT_COLORS.get(agent.name, "cyan")
                prompt = Text()
                prompt.append("  ")
                prompt.append(f"{agent.emoji} ", style=f"bold {color}")
                prompt.append(f"{agent.display_name}", style=f"bold {color}")
                prompt.append(" ▸ ", style=f"bold {color}")
                inp = console.input(prompt)
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            if not inp.strip():
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
                with console.status(
                    f"[dim]{agent.emoji} compacting...[/dim]", spinner="dots"
                ) as status_handle:
                    result = await agent.compact()
                    status_handle.stop()
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
                current = cmd_lower.lstrip("/")
                agent = agents[current]
                color = AGENT_COLORS.get(current, "white")
                console.print(
                    f"  [dim]switched to[/dim] {agent.emoji} [bold {color}]{agent.display_name}[/bold {color}]"
                )
                continue
            if cmd_lower.startswith("/"):
                _print_help()
                continue

            # --- Streaming chat with tool-call support ---
            t0 = time.monotonic()
            console.print()
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
                # Replace stream display with final response panel
                if md_content.strip():
                    live.update(
                        _build_response_panel(agent, md_content, time.monotonic() - t0, interrupted)
                    )
                live.stop()

            if not md_content.strip():
                if interrupted:
                    console.print("  [dim yellow]interrupted[/dim yellow]")
                else:
                    console.print("  [red]no response[/red]")
                continue

            console.print()

    finally:
        console.print("\n  [dim]bye[/dim]")
        await ctx.close_all()


# -- Welcome & Help --------------------------------------------------------


def _print_welcome(model: str, workspace_path: str = "") -> None:
    console.print()
    logo = (
        "[bold bright_white]"
        "        .  *  .       . *  .  *       *  .  *  .  \n"
        "     *        *    *         *    .         *      \n"
        "   ~  W E A T H E R   A G E N T S  ~             \n"
        "     .        .    .         .    *         .      \n"
        "        *  .  *       * .  *  .       .  *  .      \n"
        "[/bold bright_white]"
    )
    console.print(logo, justify="center")

    agents_info = [
        ("\U0001f32b", "雾", "Fog", "探索研究", AGENT_COLORS["fog"], "~ ~ ~"),
        ("\U0001f327", "雨", "Rain", "生成创造", AGENT_COLORS["rain"], "' ' '"),
        ("❄", "霜", "Frost", "审查优化", AGENT_COLORS["frost"], "* + *"),
        ("\U0001f328", "雪", "Snow", "规划编排", AGENT_COLORS["snow"], ". * ."),
        ("\U0001f4a7", "露", "Dew", "运维集成", AGENT_COLORS["dew"], "o o o"),
    ]

    tbl = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    for _ in agents_info:
        tbl.add_column(justify="center", ratio=1)

    icon_row = []
    name_row = []
    role_row = []
    deco_row = []
    for emoji, cn, en, role, color, deco in agents_info:
        icon_row.append(Text(emoji, style=f"bold {color}"))
        name_row.append(Text(f"{cn} {en}", style=f"bold {color}"))
        role_row.append(Text(role, style="dim"))
        deco_row.append(Text(deco, style=f"dim {color}"))

    tbl.add_row(*deco_row)
    tbl.add_row(*icon_row)
    tbl.add_row(*name_row)
    tbl.add_row(*role_row)

    console.print(tbl)
    console.print()

    status_line = Text(justify="center")
    status_line.append("  model: ", style="dim")
    status_line.append(model, style="cyan")
    status_line.append("  |  ", style="dim")
    if workspace_path:
        status_line.append("workspace: ", style="dim")
        status_line.append(workspace_path, style="magenta")
        status_line.append("  |  ", style="dim")
    status_line.append(f"v{__version__}", style="dim")
    status_line.append("  |  ", style="dim")
    status_line.append("/help", style="bold dim")
    status_line.append(" for commands", style="dim")
    console.print(status_line, justify="center")


def _print_help() -> None:
    console.print()
    sections = [
        (
            "Agents",
            [
                ("/fog /rain /frost /snow /dew", "switch agent"),
                ("/task <goal>", "multi-agent orchestration"),
            ],
        ),
        (
            "Config",
            [
                ("/model [name]", "view or switch model"),
                ("/model <agent> <model>", "per-agent model"),
                ("/apikey", "manage API keys"),
                ("/workspace", "view workspace info"),
                ("/workspace set <path>", "set custom workspace"),
                ("/workspace auto", "reset to auto-detect"),
            ],
        ),
        (
            "Skills",
            [
                ("/skills", "list available skills"),
                ("/use <skill>", "activate skill"),
                ("/deactivate", "deactivate all"),
            ],
        ),
        (
            "Info",
            [
                ("/status", "agent overview"),
                ("/cost", "token usage & cost"),
                ("/cost reset", "reset cost counter"),
                ("/compact", "compress context window"),
                ("/history", "event log"),
                ("/mcp", "MCP server status"),
                ("/version", "version info"),
            ],
        ),
        (
            "Session",
            [
                ("/sessions", "list saved sessions"),
                ("/session new [name]", "start a new session"),
                ("/session load <id>", "switch to a session"),
                ("/session delete <id>", "delete a session"),
                ("/memory", "memory stats"),
                ("/memory clear", "clear short-term memory"),
                ("/clear", "clear screen"),
                ("/quit", "exit"),
            ],
        ),
    ]
    for title, items in sections:
        console.print(f"  [bold dim]{title}[/bold dim]")
        for cmd, desc in items:
            console.print(f"    [cyan]{cmd:<30}[/cyan] [dim]{desc}[/dim]")
    console.print()


# -- Display helpers -------------------------------------------------------


def _print_status(agents: dict) -> None:
    console.print()
    tbl = Table(show_lines=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column("Agent", width=14)
    tbl.add_column("State", width=8)
    tbl.add_column("Skills", style="dim")
    tbl.add_column("Calls", justify="right")
    tbl.add_column("Tokens", justify="right")
    for a in agents.values():
        s = a.get_status()
        name = s["name"]
        color = AGENT_COLORS.get(name, "white")
        skills_str = ", ".join(sk["name"] for sk in s.get("skills", []) if sk.get("active")) or "-"
        state_color = "green" if s["state"] == "idle" else "yellow"
        tokens = f"{s['usage']['prompt_tokens']:,} / {s['usage']['completion_tokens']:,}"
        tbl.add_row(
            f"[bold {color}]{s['emoji']} {s['display_name']}[/bold {color}]",
            f"[{state_color}]{s['state']}[/{state_color}]",
            skills_str,
            str(s["usage"]["calls"]),
            tokens,
        )
    console.print(tbl)


def _print_cost(ctx) -> None:
    console.print()
    stats = ctx.llm.get_usage_stats()
    if not stats:
        console.print("  [dim]no usage yet[/dim]")
        return
    total_cost = 0.0
    for name, s in stats.items():
        cost = s.get("cost", 0.0)
        total_cost += cost
        tokens_in = f"{s.get('prompt_tokens', 0):,}"
        tokens_out = f"{s.get('completion_tokens', 0):,}"
        console.print(
            f"  [cyan]{name:<8}[/cyan]  "
            f"[dim]{s.get('calls', 0)} calls[/dim]  "
            f"{tokens_in} in / {tokens_out} out  "
            f"[green]${cost:.4f}[/green]"
        )
    console.print(f"  [bold]{'total':<8}[/bold]  [bold green]${total_cost:.4f}[/bold green]")


def _print_history(ctx) -> None:
    events = ctx.bus.get_history(limit=20)
    if not events:
        console.print("  [dim]no events yet[/dim]")
        return
    console.print()
    for e in events[-15:]:
        ts = e.timestamp.strftime("%H:%M:%S")
        summary = _summarize_event(e.type.value, e.data)
        console.print(
            f"  [dim]{ts}[/dim]  [cyan]{e.type.value:<16}[/cyan]  "
            f"[bold]{e.source:<6}[/bold]  [dim]{summary}[/dim]"
        )


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
    # Build a name -> "N tools" map from the connection report.
    connected: dict[str, str] = {}
    for line in ctx.mcp_status or []:
        if ":" in line:
            name, info = line.split(":", 1)
            connected[name.strip()] = info.strip()
    console.print()
    for s in mcp_servers:
        name = s.get("name", "?")
        transport = "stdio" if s.get("command") else "sse"
        enabled = s.get("enabled", True)
        if not enabled:
            icon, status = "[dim]○[/dim]", "[dim]disabled[/dim]"
        elif name in connected:
            icon, status = "[green]●[/green]", f"[green]{connected[name]}[/green]"
        else:
            icon, status = "[yellow]●[/yellow]", "[yellow]not connected[/yellow]"
        console.print(f"  {icon}  [cyan]{name}[/cyan]  [dim]{transport}[/dim]  {status}")


async def _print_memory_status(ctx) -> None:
    console.print()
    for ag in ctx.agent_map.values():
        short = len(ag.memory.short_term)
        working = len(ag.memory.working)
        long_term = await ag.memory.recall(limit=100)
        console.print(
            f"  {ag.emoji} {ag.display_name}  "
            f"[dim]{short} short / {working} working / "
            f"{len(long_term)} long-term[/dim]"
        )


async def _print_sessions(agent) -> None:
    sessions = await agent.memory.list_sessions()
    active_id = agent.memory.get_active_session()
    if not sessions:
        console.print("  [dim]no saved sessions[/dim]")
        console.print("  [dim]/session new [name]  — start a new session[/dim]")
        return
    console.print()
    for s in sessions:
        marker = " [green]*[/green]" if s["id"] == active_id else " "
        sid = s["id"]
        name = s["name"] or s["preview"] or "(empty)"
        if len(name) > 50:
            name = name[:47] + "..."
        count = s["message_count"]
        console.print(f" {marker} [cyan]{sid}[/cyan]  {name}  [dim]({count} msgs)[/dim]")
    console.print()
    console.print(
        "  [dim]/session new [name]     start fresh session\n"
        "  /session load <id>       switch session\n"
        "  /session delete <id>     delete session[/dim]"
    )


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
    if not skills:
        console.print(f"  [dim]{agent.display_name} has no skills[/dim]")
        return
    console.print()
    for sk in skills:
        icon = "[green]●[/green]" if sk["active"] else "[dim]○[/dim]"
        console.print(f"  {icon}  [cyan]{sk['name']:<20}[/cyan]  [dim]{sk['description']}[/dim]")


# -- Workspace management ----------------------------------------------------


def _print_workspace_path() -> None:
    """Print a table showing workspace path, drive info, and subdirectories."""
    import shutil

    cfg = load_config()
    configured = cfg.workspace.path
    is_auto = configured.lower() == "auto"
    resolved = resolve_workspace_path(configured)
    resolved_str = str(resolved.resolve())

    console.print()
    tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column("Key", style="dim")
    tbl.add_column("Value")

    mode = "[cyan]auto[/cyan]" if is_auto else "[yellow]custom[/yellow]"
    tbl.add_row("mode", mode)

    if is_auto:
        detected = detect_best_workspace_root()
        tbl.add_row("detected", str(detected))
    else:
        tbl.add_row("configured", configured)

    tbl.add_row("resolved", resolved_str)

    exists = resolved.exists()
    status = "[green]exists[/green]" if exists else "[yellow]not created[/yellow]"
    tbl.add_row("status", status)

    # Disk info
    try:
        usage = shutil.disk_usage(resolved_str)
        tbl.add_row("disk free", f"[green]{format_bytes(usage.free)}[/green]")
        tbl.add_row("disk total", format_bytes(usage.total))
    except OSError:
        tbl.add_row("disk", "[red]unavailable[/red]")

    # Subdirectories
    if exists:
        subs = []
        for child in sorted(resolved.iterdir()):
            if child.is_dir():
                subs.append(f"  [dim]{child.name}/[/dim]")
            else:
                subs.append(f"  {child.name}")
        if subs:
            tbl.add_row("contents", "\n".join(subs))

    console.print(tbl)

    if is_auto:
        console.print()
        console.print(
            "  [dim]/workspace set <path>   set custom workspace\n"
            "  /workspace auto            reset to auto-detect[/dim]"
        )

    # Also show all drives on Windows
    if os.name == "nt":
        console.print()
        console.print("  [bold dim]Available drives:[/bold dim]")
        from weather_agents.core.workspace import _get_drive_list

        for d in _get_drive_list():
            marker = " [green]*[/green]" if str(resolved).startswith(d.path) else " "
            bar = _free_bar(d.free_bytes, d.total_bytes)
            console.print(
                f" {marker} [cyan]{d.path}[/cyan]  "
                f"[green]{format_bytes(d.free_bytes):>10} free[/green]  "
                f"/ {format_bytes(d.total_bytes)}  {bar}"
            )


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
        with console.status("[dim]planning...[/dim]", spinner="dots"):
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

        console.print(f"  [bold]Plan[/bold]  [dim]{len(tasks)} tasks[/dim]")
        for t in tasks:
            emoji = emoji_map.get(t.assigned_to or "", "?")
            dep = f" [dim]<- {t.parent_id}[/dim]" if t.parent_id else ""
            console.print(f"  [dim]{t.id}.[/dim] {emoji} {t.description}{dep}")

        console.print()
        ok = sum(1 for r in results if r.success)
        total = len(results)
        color = "green" if ok == total else "yellow" if ok > 0 else "red"
        console.print(f"  [{color}]{ok}/{total} completed[/{color}]")

        if summary:
            console.print()
            console.print("  [bold]Summary[/bold]")
            md = Markdown(summary)
            console.print(md, width=min(console.width, 100))

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
    tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column("Agent", style="cyan")
    tbl.add_column("Specialty", style="dim")
    tbl.add_column("Model", style="white")
    tbl.add_column("Skills", style="dim")
    for name, cls in AGENT_CLASSES.items():
        model = getattr(ctx.config.agents, name).model or ctx.config.llm.default_model
        skills = ", ".join(cls.skill_names)
        tbl.add_row(
            f"{AGENT_EMOJI[name]} {cls.display_name} ({name})",
            cls.specialty,
            model,
            skills,
        )
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
        console.print(f"\n  [bold]model:[/bold]  {cfg.llm.default_model}")
        console.print(
            f"  [dim]temp={cfg.llm.temperature}  "
            f"max_tokens={cfg.llm.max_tokens}  "
            f"timeout={cfg.llm.timeout}s[/dim]"
        )
        console.print()
        for name in AGENT_CLASSES:
            attr = getattr(cfg.agents, name)
            m = attr.model or "(default)"
            console.print(f"  {AGENT_EMOJI[name]} {name:<6}  {m}")
        if cfg.llm.api_keys:
            console.print()
            for p, v in cfg.llm.api_keys.items():
                masked = v[:8] + "****" if len(v) > 12 else "***"
                console.print(f"  [green]●[/green]  {p}: {masked}")
        console.print(f"\n  [dim]{USER_CONFIG_DIR / 'config.yaml'}[/dim]")

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
    console.print("  [bold cyan]╭──── Weather Agents Setup ────╮[/bold cyan]")
    console.print("  [bold cyan]│  configure your agents       │[/bold cyan]")
    console.print("  [bold cyan]╰──────────────────────────────╯[/bold cyan]")

    catalog = load_model_catalog()
    if not catalog:
        console.print("\n  [red]No model catalog found. Reinstall and try again.[/red]")
        return
    flat = _flatten_catalog(catalog)

    # Step 1: choose mode
    console.print("\n  [bold]1.[/bold] How would you like to configure the 5 agents?\n")
    console.print(
        "    [cyan]1.[/cyan] [bold]Unified[/bold]   "
        "— one model + one API key for all agents [dim](recommended)[/dim]"
    )
    console.print(
        "    [cyan]2.[/cyan] [bold]Per-agent[/bold] "
        "— pick a different model for each agent [dim](advanced)[/dim]"
    )
    mode = ""
    while mode not in ("1", "2"):
        mode = console.input("\n  Choice [1/2] (Enter for 1): ").strip() or "1"
        if mode not in ("1", "2"):
            console.print("    [red]please enter 1 or 2[/red]")

    # Step 2: pick models
    providers_needed: set[str] = set()
    if mode == "1":
        console.print("\n  [bold]2.[/bold] Pick the default model for all 5 agents:")
        _print_catalog(flat)
        # Default to the first deepseek entry if present, otherwise first item.
        default_idx = next((i + 1 for i, (p, _) in enumerate(flat) if p == "deepseek"), 1)
        picked = _pick_from_catalog(flat, "\n  Model #", default_idx=default_idx)
        if not picked:
            return
        provider, model_name = picked
        set_config("default_model", model_name)
        # Reset any per-agent overrides so the default actually applies.
        for ag in AGENT_CLASSES:
            delete_config(f"model.{ag}")
        console.print(f"    [green]default → {model_name}[/green]")
        providers_needed.add(provider)
    else:
        console.print("\n  [bold]2.[/bold] Pick a model for each agent:")
        _print_catalog(flat)
        console.print()
        default_idx = next((i + 1 for i, (p, _) in enumerate(flat) if p == "deepseek"), 1)
        for agent_name, cls in AGENT_CLASSES.items():
            label = f"{AGENT_EMOJI[agent_name]} {cls.display_name} ({cls.specialty}) model #"
            picked = _pick_from_catalog(flat, label, default_idx=default_idx)
            if not picked:
                continue
            prov, model_name = picked
            set_config(f"model.{agent_name}", model_name)
            providers_needed.add(prov)
            console.print(
                f"    [green]{AGENT_EMOJI[agent_name]} {agent_name} → {model_name}[/green]"
            )

    # Step 3: collect API keys
    console.print("\n  [bold]3.[/bold]", end=" ")
    _collect_keys(providers_needed)

    console.print()
    console.print("  [green]✓ Setup complete[/green]")
    cfg_path = USER_CONFIG_DIR / "config.yaml"
    console.print(f"  [dim]saved to {cfg_path}[/dim]\n")


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
