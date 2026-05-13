"""CLI interface for Weather Agents."""

from __future__ import annotations

import asyncio
import io
import sys

import typer
from rich.console import Console

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

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


app = typer.Typer(name="wa", help="Weather Agents — 雾·雨·霜·雪·露 多智能体系统", no_args_is_help=True)
console = Console()


# -- Chat ------------------------------------------------------------------

async def _chat_single(agent_name: str, message: str) -> None:
    ctx = create_system_context()
    await ctx.init_all()
    try:
        agent = ctx.agent_map.get(agent_name)
        if not agent:
            console.print(f"[red]Unknown agent: {agent_name}[/red]")
            return
        with console.status(f"{agent.display_name} 思考中..."):
            resp = await agent.chat(message)
        console.print(Panel(
            Markdown(resp), title=f"{agent.emoji} {agent.display_name}", border_style="green",
        ))
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
        console.print()

        while True:
            try:
                inp = console.input(f"[bold cyan]{agent.emoji} {agent.display_name}> [/]")
            except (EOFError, KeyboardInterrupt):
                break
            if not inp.strip():
                continue

            cmd = inp.strip()
            cmd_lower = cmd.lower()

            if cmd_lower in ("/quit", "/exit"):
                break
            if cmd_lower in ("/help", "/?"):
                _print_help()
                continue
            if cmd_lower == "/clear":
                console.clear()
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
                    console.print(f"[green]✅ 技能 [{skill_name}] 已激活[/green]")
                else:
                    console.print(f"[red]未知技能: {skill_name}（输入 /skills 查看可用技能）[/red]")
                continue
            if cmd_lower == "/deactivate":
                agent.deactivate_all_skills()
                console.print("[green]所有技能已关闭，恢复基础提示词[/green]")
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
            if cmd_lower.lstrip("/") in AGENT_CLASSES:
                current = cmd_lower.lstrip("/")
                agent = agents[current]
                console.print(f"→ {agent.emoji} {agent.display_name}")
                continue
            if cmd_lower.startswith("/"):
                _print_help()
                continue

            # Streaming chat
            console.print()
            with Live("", console=console, refresh_per_second=16) as live:
                full = ""
                async for chunk in agent.chat_stream(inp):
                    full += chunk
                    live.update(Panel(
                        Markdown(full),
                        title=f"{agent.emoji} {agent.display_name}",
                        border_style="green",
                    ))
    finally:
        console.print("[dim]正在关闭...[/dim]")
        await ctx.close_all()


def _print_welcome(model: str) -> None:
    from rich.text import Text

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
        ("🌫", "雾", "Fog",   "探索研究", "bright_white", "~ ~ ~"),
        ("🌧", "雨", "Rain",  "生成创造", "blue",         "' ' '"),
        ("❄",  "霜", "Frost", "审查优化", "cyan",         "* + *"),
        ("🌨", "雪", "Snow",  "规划编排", "bright_white", ". * ."),
        ("💧", "露", "Dew",   "运维集成", "green",        "o o o"),
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
    status_line.append(" 查看命令", style="dim")
    console.print(status_line, justify="center")


def _print_help() -> None:
    tbl = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tbl.add_column(style="bold cyan", width=24)
    tbl.add_column(style="dim")
    tbl.add_row("/fog /rain /frost /snow /dew", "切换 Agent")
    tbl.add_row("/task <目标>", "多 Agent 协作编排")
    tbl.add_row("/model [名称]", "查看或切换模型")
    tbl.add_row("/model <agent> <模型>", "指定 Agent 模型")
    tbl.add_row("/apikey", "查看 API Key")
    tbl.add_row("/apikey set <provider> <key>", "添加/替换 Key")
    tbl.add_row("/apikey del <provider>", "删除 Key")
    tbl.add_row("/skills", "查看可用技能")
    tbl.add_row("/use <技能>", "激活技能")
    tbl.add_row("/deactivate", "关闭所有技能")
    tbl.add_row("/status", "Agent 状态")
    tbl.add_row("/cost", "Token 用量和费用")
    tbl.add_row("/history", "事件日志")
    tbl.add_row("/mcp", "MCP 服务器状态")
    tbl.add_row("/clear", "清屏")
    tbl.add_row("/quit", "退出")
    console.print(tbl)


def _print_status(agents: dict) -> None:
    tbl = Table(title="Agent 状态", show_lines=True)
    tbl.add_column("Agent", style="cyan", width=12)
    tbl.add_column("状态", width=10)
    tbl.add_column("激活技能", style="dim")
    tbl.add_column("调用次数", justify="right")
    tbl.add_column("Token", justify="right")
    for a in agents.values():
        s = a.get_status()
        skills_str = ", ".join(
            sk["name"] for sk in s.get("skills", []) if sk.get("active")
        ) or "—"
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
    tbl = Table(title="费用统计")
    tbl.add_column("Agent", style="cyan")
    tbl.add_column("调用次数", justify="right")
    tbl.add_column("输入 Token", justify="right")
    tbl.add_column("输出 Token", justify="right")
    tbl.add_column("费用 (USD)", justify="right", style="green")

    stats = ctx.llm.get_usage_stats()
    total_cost = 0.0
    for name, s in stats.items():
        cost = s.get("cost", 0.0)
        total_cost += cost
        tbl.add_row(
            name,
            str(s.get("calls", 0)),
            f'{s.get("prompt_tokens", 0):,}',
            f'{s.get("completion_tokens", 0):,}',
            f'${cost:.6f}',
        )
    tbl.add_row("", "", "", "[bold]总计[/bold]", f'[bold]${total_cost:.6f}[/bold]')
    console.print(tbl)


def _print_history(ctx) -> None:
    events = ctx.bus.get_history(limit=20)
    if not events:
        console.print("[dim]暂无事件历史[/dim]")
        return
    tbl = Table(title="最近事件", show_lines=True)
    tbl.add_column("时间", style="dim", width=12)
    tbl.add_column("类型", style="cyan", width=16)
    tbl.add_column("来源", width=8)
    tbl.add_column("数据")
    for e in events[-20:]:
        ts = e.timestamp.strftime("%H:%M:%S")
        data_str = str(e.data)[:80] if e.data else ""
        tbl.add_row(ts, e.type.value, e.source, data_str)
    console.print(tbl)


def _print_mcp_status(ctx) -> None:
    mcp_servers = ctx.config.mcp.servers
    if not mcp_servers:
        console.print("[dim]未配置 MCP 服务器。编辑 ~/.weather-agents/config.yaml 添加。[/dim]")
        return
    tbl = Table(title="MCP 服务器")
    tbl.add_column("名称", style="cyan")
    tbl.add_column("类型")
    tbl.add_column("状态")
    for s in mcp_servers:
        transport = "stdio" if s.get("command") else "SSE"
        enabled = "[green]启用[/green]" if s.get("enabled", True) else "[dim]禁用[/dim]"
        tbl.add_row(s.get("name", "?"), transport, enabled)
    console.print(tbl)


def _print_skills(agent) -> None:
    skills = agent.get_available_skills()
    if not skills:
        console.print(f"[dim]{agent.display_name} 没有可用技能[/dim]")
        return
    tbl = Table(title=f"{agent.emoji} {agent.display_name} 技能")
    tbl.add_column("技能", style="cyan")
    tbl.add_column("描述", style="white")
    tbl.add_column("状态", style="green", width=8)
    for sk in skills:
        status = "✅ 激活" if sk["active"] else "  待用"
        tbl.add_row(sk["name"], sk["description"], status)
    console.print(tbl)


# -- Model & API key management --------------------------------------------

def _handle_model_command(cmd: str, ctx) -> None:
    """Handle /model [name] — view or switch the default model at runtime."""
    import os
    parts = cmd.strip().split(maxsplit=1)
    if len(parts) == 1:
        # /model — show current model info
        current = ctx.config.llm.default_model
        console.print(f"[bold]当前模型:[/bold] {current}")
        console.print()
        tbl = Table(title="各 Agent 模型配置")
        tbl.add_column("Agent", style="cyan")
        tbl.add_column("模型", style="white")
        for name in AGENT_CLASSES:
            agent_cfg = getattr(ctx.config.agents, name, None)
            model = (agent_cfg.model if agent_cfg and agent_cfg.model else current)
            is_default = "" if agent_cfg and agent_cfg.model else " [dim](default)[/dim]"
            tbl.add_row(f"{AGENT_EMOJI[name]} {name}", f"{model}{is_default}")
        console.print(tbl)
        console.print(
            "\n[dim]切换: /model <模型名>  (如 deepseek/deepseek-chat, gpt-4o)\n"
            "指定Agent: /model fog <模型名>\n"
            "重置Agent: /model fog default[/dim]"
        )
        return

    arg = parts[1].strip()
    tokens = arg.split(maxsplit=1)

    # /model <agent> <model> — set agent-specific model
    if len(tokens) == 2 and tokens[0] in AGENT_CLASSES:
        agent_name, model_name = tokens
        if model_name.lower() == "default":
            ok, msg = delete_config(f"model.{agent_name}")
            if ok:
                agent_cfg = getattr(ctx.config.agents, agent_name)
                agent_cfg.model = ""
            console.print(f"[green]{AGENT_EMOJI[agent_name]} {agent_name} → 使用默认模型[/green]")
        else:
            ok, msg = set_config(f"model.{agent_name}", model_name)
            if ok:
                agent_cfg = getattr(ctx.config.agents, agent_name)
                agent_cfg.model = model_name
            console.print(f"[green]{AGENT_EMOJI[agent_name]} {agent_name} → {model_name}[/green]")
        return

    # /model <model> — set default model
    model_name = arg
    ok, msg = set_config("default_model", model_name)
    if ok:
        ctx.config.llm.default_model = model_name
        console.print(f"[green]默认模型 → {model_name}[/green]")
    else:
        console.print(f"[red]{msg}[/red]")


def _handle_apikey_command(cmd: str, ctx) -> None:
    """Handle /apikey — manage API keys interactively."""
    import os
    parts = cmd.strip().split(maxsplit=2)

    if len(parts) == 1:
        # /apikey — list all keys
        keys = ctx.config.llm.api_keys
        if not keys:
            console.print("[dim]未配置任何 API Key[/dim]")
        else:
            tbl = Table(title="API Keys")
            tbl.add_column("Provider", style="cyan")
            tbl.add_column("Key", style="white")
            tbl.add_column("状态", style="green")
            for provider, key in keys.items():
                masked = key[:8] + "****" + key[-4:] if len(key) > 16 else key[:4] + "****"
                tbl.add_row(provider, masked, "✓ 已配置")
            console.print(tbl)
        console.print(
            "\n[dim]添加/替换: /apikey set <provider> <key>\n"
            "删除:      /apikey del <provider>\n"
            "provider: openai, anthropic, deepseek, ...[/dim]"
        )
        return

    action = parts[1].lower()

    if action in ("set", "add") and len(parts) == 3:
        # /apikey set provider key
        tokens = parts[2].strip().split(maxsplit=1)
        if len(tokens) != 2:
            console.print("[red]用法: /apikey set <provider> <key>[/red]")
            return
        provider, key = tokens
        provider = provider.lower()
        ok, msg = set_config(f"api_key.{provider}", key)
        if ok:
            ctx.config.llm.api_keys[provider] = key
            _sync_api_keys_to_env({provider: key})
            masked = key[:8] + "****"
            console.print(f"[green]✓ {provider} API Key 已设置 ({masked})[/green]")
        else:
            console.print(f"[red]{msg}[/red]")
        return

    if action in ("del", "delete", "rm", "remove"):
        if len(parts) < 3:
            console.print("[red]用法: /apikey del <provider>[/red]")
            return
        provider = parts[2].strip().lower()
        ok, msg = delete_config(f"api_key.{provider}")
        if ok:
            ctx.config.llm.api_keys.pop(provider, None)
            from weather_agents.core.config import _ENV_KEY_MAP
            env_var = _ENV_KEY_MAP.get(provider, f"{provider.upper()}_API_KEY")
            os.environ.pop(env_var, None)
            console.print(f"[green]✓ {provider} API Key 已删除[/green]")
        else:
            console.print(f"[red]{msg}[/red]")
        return

    console.print("[red]用法: /apikey [set <provider> <key> | del <provider>][/red]")


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
        with console.status("❄️ Snow 分解任务..."):
            tasks = await snow.orchestrate(goal)

        tbl = Table(title="任务计划", show_lines=True)
        tbl.add_column("ID", style="cyan", width=4)
        tbl.add_column("Agent", style="magenta", width=10)
        tbl.add_column("描述", style="white")
        tbl.add_column("依赖", style="dim", width=8)
        for t in tasks:
            emoji = emoji_map.get(t.assigned_to or "", "❓")
            dep = t.parent_id or "—"
            tbl.add_row(t.id, f"{emoji} {t.assigned_to or '?'}", t.description, dep)
        console.print(tbl)

        from weather_agents.core.agent import Task as AgentTask

        results = {}
        for t in tasks:
            if not t.assigned_to or t.assigned_to == "snow":
                continue
            a = agents.get(t.assigned_to)
            if not a:
                continue
            emoji = emoji_map.get(t.assigned_to, "❓")
            console.print(f"\n{emoji} [bold]{t.description}[/bold]")
            with console.status(f"  {a.display_name} 工作中..."):
                r = await a.execute_task(AgentTask(
                    id=t.id,
                    description=t.description,
                    assigned_to=t.assigned_to,
                    metadata=t.metadata,
                ))
                results[t.id] = r
            icon = "[green]✓[/green]" if r.success else "[red]✗[/red]"
            console.print(f"  {icon} {r.content[:200]}")

        ok = sum(1 for r in results.values() if r.success)
        total = len(results)
        color = "green" if ok == total else "yellow" if ok > 0 else "red"
        console.print(Panel(f"完成: {ok}/{total}", border_style=color))

        if results:
            summary_prompt = "汇总以下子任务结果：\n\n"
            for t in tasks:
                if t.id in results:
                    r = results[t.id]
                    status = "✅" if r.success else "❌"
                    summary_prompt += (
                        f"## 任务 {t.id} ({t.assigned_to}) {status}\n"
                        f"{r.content[:500]}\n\n"
                    )
            with console.status("❄️ 汇总中..."):
                s = await snow.chat(summary_prompt)
            console.print(Panel(Markdown(s), title="❄️ 执行总结", border_style="cyan"))
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
    tbl = Table(title="Weather Agents", show_lines=True)
    tbl.add_column("Agent", style="cyan")
    tbl.add_column("专长", style="magenta")
    tbl.add_column("模型", style="dim")
    tbl.add_column("技能", style="white")
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
        console.print(f"[bold]Default model:[/bold] {cfg.llm.default_model}")
        console.print(
            f"Temperature: {cfg.llm.temperature}  |  "
            f"Max tokens: {cfg.llm.max_tokens}  |  "
            f"Timeout: {cfg.llm.timeout}s"
        )
        console.print()
        console.print("[bold]Agents:[/bold]")
        for name in AGENT_CLASSES:
            attr = getattr(cfg.agents, name)
            m = attr.model or "(default)"
            console.print(f"  {AGENT_EMOJI[name]} {name}: model={m}, specialty={attr.specialty}")
        if cfg.llm.api_keys:
            console.print("\n[bold]API keys:[/bold]")
            for p, v in cfg.llm.api_keys.items():
                masked = v[:8] + "****" if len(v) > 12 else "***"
                console.print(f"  {p}: {masked}")
        console.print(f"\n[dim]User config: {USER_CONFIG_DIR / 'config.yaml'}[/dim]")

    elif action == "set":
        if not key or value is None:
            console.print("[red]Usage: wa config set <key> <value>[/red]")
            raise typer.Exit(1)
        ok, msg = set_config(key, value)
        color = "green" if ok else "red"
        console.print(f"[{color}]{msg}[/{color}]")

    elif action == "delete":
        if not key:
            console.print("[red]Usage: wa config delete <key>[/red]")
            raise typer.Exit(1)
        ok, msg = delete_config(key)
        color = "green" if ok else "red"
        console.print(f"[{color}]{msg}[/{color}]")

    elif action == "models":
        catalog = load_model_catalog()
        if not catalog:
            console.print("[yellow]No models.yaml found.[/yellow]")
            return
        console.print(format_models_for_display(catalog))

    else:
        console.print(f"[red]Unknown action: {action} (use: list / set / delete / models)[/red]")


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
                        console.print(f"[red]Unknown agent: {name}[/red]")
                        continue
                    count = len(agent.memory.short_term)
                    await agent.memory.clear_short_term()
                    console.print(
                        f"[green]Cleared {agent.emoji} {agent.display_name} "
                        f"memory ({count} messages)[/green]"
                    )
            else:
                for name, agent in ctx.agent_map.items():
                    short = len(agent.memory.short_term)
                    working = len(agent.memory.working)
                    long_term = await agent.memory.recall(limit=100)
                    console.print(
                        f"{agent.emoji} {agent.display_name}: "
                        f"{short} short-term, {working} working, "
                        f"{len(long_term)} long-term"
                    )
        finally:
            await ctx.close_all()

    asyncio.run(_run())


# -- Web -------------------------------------------------------------------

@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8765, help="Bind port"),
) -> None:
    """Start the web dashboard."""
    from weather_agents.web.app import create_app
    import uvicorn

    console.print(f"[bold]Weather Agents Dashboard[/bold] -> http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port)


# -- Version ---------------------------------------------------------------

@app.command()
def version() -> None:
    """Show version information."""
    from weather_agents import __version__

    console.print(f"Weather Agents v{__version__}")
