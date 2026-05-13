"""CLI interface for Weather Agents — terminal agent product."""

from __future__ import annotations

import asyncio
import os
import sys
import time

import typer
from rich.console import Console
from rich.rule import Rule

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from weather_agents.core.config import (
    delete_config,
    load_config,
    load_model_catalog,
    format_models_for_display,
    set_config,
    _sync_api_keys_to_env,
    USER_CONFIG_DIR,
)
from weather_agents import __version__
from weather_agents.core.factory import create_system_context, AGENT_CLASSES, AGENT_EMOJI


app = typer.Typer(name="wa", help="Weather Agents CLI", no_args_is_help=True)
console = Console()


# -- Spinner + status chat -------------------------------------------------

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
        _print_response(agent, resp, elapsed)
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
        _print_welcome(model)

        while True:
            console.print()
            try:
                prompt = Text()
                prompt.append(f"{agent.emoji} ", style="bold")
                prompt.append(f"{agent.display_name}", style="bold cyan")
                prompt.append(" > ", style="dim")
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
                _print_welcome(ctx.config.llm.default_model)
                continue
            if cmd_lower == "/status":
                _print_status(agents)
                continue
            if cmd_lower == "/cost":
                _print_cost(ctx)
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
                    console.print(f"  [red]unknown skill: {skill_name}[/red] [dim](/skills to list)[/dim]")
                continue
            if cmd_lower == "/deactivate":
                agent.deactivate_all_skills()
                console.print("  [dim]skills deactivated[/dim]")
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
                console.print(
                    f"  [dim]switched to[/dim] {agent.emoji} "
                    f"[bold]{agent.display_name}[/bold]"
                )
                continue
            if cmd_lower.startswith("/"):
                _print_help()
                continue

            # --- Spinner-based chat (no streaming) ---
            t0 = time.monotonic()
            console.print()
            interrupted = False
            status_handle = console.status(
                f"[dim]{agent.emoji} thinking...[/dim]",
                spinner="dots",
            )
            status_handle.start()

            def _on_status(msg: str) -> None:
                status_handle.update(f"[dim]{agent.emoji} {msg}[/dim]")

            try:
                resp = await agent.chat(inp, on_status=_on_status)
            except KeyboardInterrupt:
                interrupted = True
                resp = ""
            finally:
                status_handle.stop()

            elapsed = time.monotonic() - t0

            if not resp.strip():
                if interrupted:
                    console.print("  [dim yellow]interrupted[/dim yellow]")
                else:
                    console.print("  [red]no response[/red]")
                continue

            _print_response(agent, resp, elapsed, interrupted)

    finally:
        console.print(f"\n  [dim]bye[/dim]")
        await ctx.close_all()


def _print_response(
    agent, content: str, elapsed: float, interrupted: bool = False,
) -> None:
    """Print a finalized response with metadata footer."""
    header = Text()
    header.append(f"  {agent.emoji} ", style="bold")
    header.append(agent.display_name, style="bold")
    console.print(header)

    # Render as markdown for code blocks, lists, etc.
    md = Markdown(content)
    console.print(md, width=min(console.width, 100))

    # Footer: timing + interrupt notice
    footer = Text("  ")
    footer.append(f"{elapsed:.1f}s", style="dim")
    if interrupted:
        footer.append("  (interrupted)", style="dim yellow")
    console.print(footer)


# -- Welcome & Help --------------------------------------------------------

def _print_welcome(model: str) -> None:
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
        ("\U0001f32b", "雾", "Fog",   "探索研究", "bright_white", "~ ~ ~"),
        ("\U0001f327", "雨", "Rain",  "生成创造", "blue",         "' ' '"),
        ("❄",     "霜", "Frost", "审查优化", "cyan",         "* + *"),
        ("\U0001f328", "雪", "Snow",  "规划编排", "bright_white", ". * ."),
        ("\U0001f4a7", "露", "Dew",   "运维集成", "green",        "o o o"),
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
                ("/history", "event log"),
                ("/mcp", "MCP server status"),
                ("/version", "version info"),
            ],
        ),
        (
            "Session",
            [
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
    tbl.add_column("Agent", style="cyan", width=14)
    tbl.add_column("State", width=8)
    tbl.add_column("Skills", style="dim")
    tbl.add_column("Calls", justify="right")
    tbl.add_column("Tokens", justify="right")
    for a in agents.values():
        s = a.get_status()
        skills_str = ", ".join(
            sk["name"] for sk in s.get("skills", []) if sk.get("active")
        ) or "-"
        state_color = "green" if s["state"] == "idle" else "yellow"
        tokens = f'{s["usage"]["prompt_tokens"]:,} / {s["usage"]["completion_tokens"]:,}'
        tbl.add_row(
            f'{s["emoji"]} {s["display_name"]}',
            f'[{state_color}]{s["state"]}[/{state_color}]',
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
        tokens_in = f'{s.get("prompt_tokens", 0):,}'
        tokens_out = f'{s.get("completion_tokens", 0):,}'
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
        data_str = str(e.data)[:60] if e.data else ""
        console.print(
            f"  [dim]{ts}[/dim]  [cyan]{e.type.value:<16}[/cyan]  "
            f"[bold]{e.source:<6}[/bold]  [dim]{data_str}[/dim]"
        )


def _print_mcp_status(ctx) -> None:
    mcp_servers = ctx.config.mcp.servers
    if not mcp_servers:
        console.print("  [dim]no MCP servers configured[/dim]")
        return
    console.print()
    for s in mcp_servers:
        transport = "stdio" if s.get("command") else "sse"
        enabled = s.get("enabled", True)
        icon = "[green]●[/green]" if enabled else "[dim]○[/dim]"
        console.print(f"  {icon}  [cyan]{s.get('name', '?')}[/cyan]  [dim]{transport}[/dim]")


def _print_skills(agent) -> None:
    skills = agent.get_available_skills()
    if not skills:
        console.print(f"  [dim]{agent.display_name} has no skills[/dim]")
        return
    console.print()
    for sk in skills:
        icon = "[green]●[/green]" if sk["active"] else "[dim]○[/dim]"
        console.print(f"  {icon}  [cyan]{sk['name']:<20}[/cyan]  [dim]{sk['description']}[/dim]")


# -- Model & API key management --------------------------------------------

def _handle_model_command(cmd: str, ctx) -> None:
    parts = cmd.strip().split(maxsplit=1)
    if len(parts) == 1:
        current = ctx.config.llm.default_model
        console.print(f"\n  [bold]default:[/bold] [cyan]{current}[/cyan]\n")
        for name in AGENT_CLASSES:
            agent_cfg = getattr(ctx.config.agents, name, None)
            m = (agent_cfg.model if agent_cfg and agent_cfg.model else current)
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
            console.print(f"  [green]{AGENT_EMOJI[agent_name]} {agent_name} -> {model_name}[/green]")
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
                console.print(f"  [green]●[/green]  [cyan]{provider:<12}[/cyan]  [dim]{masked}[/dim]")
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

    snow = agents["snow"]
    emoji_map = AGENT_EMOJI

    try:
        console.print()
        with console.status("[dim]planning...[/dim]", spinner="dots"):
            tasks = await snow.orchestrate(goal)

        if not tasks:
            console.print("  [dim]no tasks generated[/dim]")
            return

        console.print(f"  [bold]Plan[/bold]  [dim]{len(tasks)} tasks[/dim]")
        for t in tasks:
            emoji = emoji_map.get(t.assigned_to or "", "?")
            dep = f" [dim]<- {t.parent_id}[/dim]" if t.parent_id else ""
            console.print(f"  [dim]{t.id}.[/dim] {emoji} {t.description}{dep}")

        console.print()
        from weather_agents.core.agent import Task as AgentTask

        results = {}
        for t in tasks:
            if not t.assigned_to or t.assigned_to == "snow":
                continue
            a = agents.get(t.assigned_to)
            if not a:
                continue
            emoji = emoji_map.get(t.assigned_to, "?")

            t0 = time.monotonic()
            status_handle = console.status(
                f"[dim]{emoji} {t.description}...[/dim]",
                spinner="dots",
            )
            status_handle.start()

            def _make_cb(em, sh):
                def _cb(msg):
                    sh.update(f"[dim]{em} {msg}[/dim]")
                return _cb

            try:
                r = await a.execute_task(
                    AgentTask(
                        id=t.id,
                        description=t.description,
                        assigned_to=t.assigned_to,
                        metadata=t.metadata,
                    ),
                    on_status=_make_cb(emoji, status_handle),
                )
            finally:
                status_handle.stop()

            results[t.id] = r
            elapsed = time.monotonic() - t0
            icon = "[green]✓[/green]" if r.success else "[red]✗[/red]"
            console.print(f"  {icon} {emoji} {t.description}  [dim]{elapsed:.1f}s[/dim]")

        ok = sum(1 for r in results.values() if r.success)
        total = len(results)
        color = "green" if ok == total else "yellow" if ok > 0 else "red"
        console.print(f"\n  [{color}]{ok}/{total} completed[/{color}]")

        if results:
            summary_prompt = "Summarize the following subtask results:\n\n"
            for t in tasks:
                if t.id in results:
                    r = results[t.id]
                    status = "OK" if r.success else "FAIL"
                    summary_prompt += (
                        f"## Task {t.id} ({t.assigned_to}) {status}\n"
                        f"{r.content[:500]}\n\n"
                    )
            console.print()
            with console.status("[dim]summarizing...[/dim]", spinner="dots"):
                s = await snow.chat(summary_prompt)

            console.print(f"  [bold]Summary[/bold]")
            md = Markdown(s)
            console.print(md, width=min(console.width, 100))
    finally:
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
                    count = len(agent.memory.short_term)
                    await agent.memory.clear_short_term()
                    console.print(
                        f"  [green]cleared {agent.emoji} {agent.display_name} "
                        f"({count} messages)[/green]"
                    )
            else:
                for name, agent in ctx.agent_map.items():
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


# -- Version ---------------------------------------------------------------

@app.command()
def version() -> None:
    """Show version information."""
    console.print(f"  Weather Agents [bold]v{__version__}[/bold]")
