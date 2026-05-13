"""CLI interface for Weather Agents."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from weather_agents.core.config import load_config, set_config, delete_config, load_model_catalog, format_models_for_display, USER_CONFIG_DIR
from weather_agents.core.factory import create_system_context, AGENT_CLASSES, AGENT_EMOJI


app = typer.Typer(name="wa", help="Weather Agents — 雾·雨·霜·雪·露 多智能体系统", no_args_is_help=True)
console = Console()


# ── Chat ───────────────────────────────────────────────────────────────────

async def _chat_single(agent_name: str, message: str):
    ctx = create_system_context()
    await ctx.init_all()
    try:
        agent = ctx.agent_map.get(agent_name)
        if not agent:
            console.print(f"[red]Unknown agent: {agent_name}[/red]")
            return
        with console.status(f"{agent.display_name} 思考中..."):
            resp = await agent.chat(message)
        console.print(Panel(resp, title=f"{agent.emoji} {agent.display_name}", border_style="green"))
    finally:
        await ctx.close_all()


async def _interactive(agent_name: str | None = None):
    ctx = create_system_context()
    await ctx.init_all()
    try:
        agents = ctx.agent_map
        console.print(Panel(
            "[bold]Weather Agents[/bold]\n"
            "/fog /rain /frost /snow /dew 切换  |  /task <目标> 编排  |  /skills 技能  |  /use <技能> 激活\n"
            "/status 状态  |  /deactivate 关闭技能  |  /clear 清屏  |  /quit 退出",
            title="Welcome", border_style="cyan",
        ))
        current = agent_name or "fog"
        agent = agents[current]

        while True:
            try:
                inp = console.input(f"[bold cyan]{agent.emoji} {agent.display_name}> [/]")
            except (EOFError, KeyboardInterrupt):
                break
            if not inp.strip():
                continue

            cmd = inp.strip().lower()
            if cmd in ("/quit", "/exit"):
                break
            if cmd == "/clear":
                console.clear()
                continue
            if cmd == "/status":
                for a in agents.values():
                    s = a.get_status()
                    skills_str = ""
                    if s.get("skills"):
                        active = [sk["name"] for sk in s["skills"] if sk.get("active")]
                        if active:
                            skills_str = f" [dim]skills: {', '.join(active)}[/dim]"
                    console.print(f"  {s['emoji']} {s['display_name']}: [bold]{s['state']}[/bold]{skills_str}")
                continue
            if cmd == "/skills":
                skills = agent.get_available_skills()
                if not skills:
                    console.print("[dim]{agent.display_name} 没有可用技能[/dim]")
                else:
                    from rich.table import Table
                    tbl = Table(title=f"{agent.emoji} {agent.display_name} 技能")
                    tbl.add_column("技能", style="cyan")
                    tbl.add_column("描述", style="white")
                    tbl.add_column("状态", style="green", width=8)
                    for sk in skills:
                        status = "✅ 激活" if sk["active"] else "  待用"
                        tbl.add_row(sk["name"], sk["description"], status)
                    console.print(tbl)
                continue
            if cmd.startswith("/use "):
                skill_name = cmd[5:]
                if agent.activate_skill(skill_name):
                    console.print(f"[green]✅ 技能 [{skill_name}] 已激活[/green]")
                else:
                    console.print(f"[red]未知技能: {skill_name}（输入 /skills 查看可用技能）[/red]")
                continue
            if cmd == "/deactivate":
                agent.deactivate_all_skills()
                console.print("[green]所有技能已关闭，恢复基础提示词[/green]")
                continue
            if cmd.startswith("/task "):
                goal = cmd[6:]
                if goal:
                    await _run_task(goal, agents)
                continue
            if cmd.lstrip("/") in AGENT_CLASSES:
                current = cmd.lstrip("/")
                agent = agents[current]
                console.print(f"→ {agent.emoji} {agent.display_name}")
                continue
            if cmd.startswith("/"):
                console.print("[dim]命令: /fog /rain /frost /snow /dew /task /skills /use /deactivate /status /clear /quit[/dim]")
                continue

            # Streaming chat
            console.print()
            with Live("", console=console, refresh_per_second=16) as live:
                full = ""
                async for chunk in agent.chat_stream(inp):
                    full += chunk
                    live.update(Panel(Markdown(full), title=f"{agent.emoji} {agent.display_name}", border_style="green"))
    finally:
        await ctx.close_all()


# ── Task orchestration ─────────────────────────────────────────────────────

async def _run_task(goal: str, agents=None):
    own_ctx = None
    if agents is None:
        own_ctx = create_system_context()
        await own_ctx.init_all()
        agents = own_ctx.agent_map

    snow = agents["snow"]
    emoji_map = AGENT_EMOJI

    try:
        with console.status("🌨️ 分解任务..."):
            tasks = await snow.orchestrate(goal)

        tbl = Table(title="任务计划", show_lines=True)
        tbl.add_column("ID", style="cyan", width=4)
        tbl.add_column("Agent", style="magenta", width=10)
        tbl.add_column("描述", style="white")
        for t in tasks:
            emoji = emoji_map.get(t.assigned_to or "", "❓")
            tbl.add_row(t.id, f"{emoji} {t.assigned_to or '?'}", t.description)
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
            console.print(f"{emoji} {t.description}")
            with console.status(f"{a.display_name} 工作中..."):
                r = await a.execute_task(AgentTask(id=t.id, description=t.description, assigned_to=t.assigned_to, metadata=t.metadata))
                results[t.id] = r
            console.print(f"  {'[green]✓[/green]' if r.success else '[red]✗[/red]'}")

        ok = sum(1 for r in results.values() if r.success)
        console.print(Panel(f"完成: {ok}/{len(results)}", border_style="green" if ok == len(results) else "yellow"))

        if results:
            summary = "汇总以下子任务结果：\n"
            for t in tasks:
                if t.id in results:
                    r = results[t.id]
                    summary += f"## 任务 {t.id} ({t.assigned_to}) {'✅' if r.success else '❌'}\n{r.content[:500]}\n\n"
            with console.status("🌨️ 汇总中..."):
                s = await snow.chat(summary)
            console.print(Panel(s, title="🌨️ 执行总结", border_style="cyan"))
    finally:
        if own_ctx:
            await own_ctx.close_all()


# ── CLI commands ───────────────────────────────────────────────────────────

@app.command()
def chat(agent: str = typer.Argument("fog"), message: str = typer.Argument(None)):
    """Chat with an agent (fog/rain/frost/snow/dew)."""
    if message:
        asyncio.run(_chat_single(agent, message))
    else:
        asyncio.run(_interactive(agent))


@app.command()
def task(goal: str = typer.Argument(..., help="Task goal")):
    """Multi-agent orchestration."""
    asyncio.run(_run_task(goal))


@app.command()
def status():
    """Show all agent status."""
    ctx = create_system_context()
    tbl = Table(title="Weather Agents Status")
    tbl.add_column("Agent", style="cyan")
    tbl.add_column("专长", style="magenta")
    tbl.add_column("模型", style="dim")
    for name, cls in AGENT_CLASSES.items():
        model = getattr(ctx.config.agents, name).model or ctx.config.llm.default_model
        tbl.add_row(f"{AGENT_EMOJI[name]} {cls.display_name} ({name})", cls.specialty, model)
    console.print(tbl)


# ── Config ─────────────────────────────────────────────────────────────────

@app.command()
def config(
    action: str = typer.Argument("list", help="list / set / delete / models"),
    key: str = typer.Argument(None, help="Config key (e.g. model.fog, api_key.openai, temperature)"),
    value: str = typer.Argument(None, help="Config value (for set)"),
):
    """Manage configuration."""
    if action == "list":
        cfg = load_config()
        console.print(f"[bold]Default model:[/bold] {cfg.llm.default_model}")
        console.print(f"Temperature: {cfg.llm.temperature}  |  Max tokens: {cfg.llm.max_tokens}  |  Timeout: {cfg.llm.timeout}s")
        console.print()
        console.print("[bold]Agents:[/bold]")
        for name in AGENT_CLASSES:
            attr = getattr(cfg.agents, name)
            m = attr.model or "(default)"
            console.print(f"  {AGENT_EMOJI[name]} {name}: model={m}, specialty={attr.specialty}")
        if cfg.llm.api_keys:
            console.print("\n[bold]API keys:[/bold]")
            for p in cfg.llm.api_keys:
                v = cfg.llm.api_keys[p]
                masked = v[:8] + "****" if len(v) > 12 else "***"
                console.print(f"  {p}: {masked}")
        console.print(f"\n[dim]User config: {USER_CONFIG_DIR / 'config.yaml'}[/dim]")

    elif action == "set":
        if not key or value is None:
            console.print("[red]Usage: wa config set <key> <value>[/red]")
            raise typer.Exit(1)
        ok, msg = set_config(key, value)
        (console.print if ok else console.print)[f"[{'green' if ok else 'red'}]{msg}[/{'green' if ok else 'red'}]"]

    elif action == "delete":
        if not key:
            console.print("[red]Usage: wa config delete <key>[/red]")
            raise typer.Exit(1)
        ok, msg = delete_config(key)
        (console.print if ok else console.print)[f"[{'green' if ok else 'red'}]{msg}[/{'green' if ok else 'red'}]"]

    elif action == "models":
        catalog = load_model_catalog()
        if not catalog:
            console.print("[yellow]No models.yaml found.[/yellow]")
            return
        console.print(format_models_for_display(catalog))

    else:
        console.print(f"[red]Unknown action: {action} (use: list / set / delete / models)[/red]")


# ── Memory ─────────────────────────────────────────────────────────────────

@app.command()
def memory(
    action: str = typer.Argument("status", help="status / clear"),
    agent_name: str = typer.Argument(None, help="Agent name or omit for all"),
):
    """Manage agent memory."""
    ctx = create_system_context()

    if action == "clear":
        targets = [agent_name] if agent_name else list(ctx.agent_map.keys())
        for name in targets:
            agent = ctx.agent_map.get(name)
            if not agent:
                console.print(f"[red]Unknown agent: {name}[/red]")
                continue
            asyncio.run(agent.init())
            count = len(agent.memory.short_term)
            agent.memory.clear_short_term()
            console.print(f"[green]Cleared {agent.emoji} {agent.display_name} memory ({count} messages)[/green]")

    else:  # status
        for name, cls in AGENT_CLASSES.items():
            agent = ctx.agent_map[name]
            asyncio.run(agent.init())
            short = len(agent.memory.short_term)
            working = len(agent.memory.working)
            console.print(f"{AGENT_EMOJI[name]} {cls.display_name}: {short} short-term, {working} working vars")


# ── Web ────────────────────────────────────────────────────────────────────

@app.command()
def web(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8765)):
    """Start the web dashboard."""
    from weather_agents.web.app import create_app
    import uvicorn
    uvicorn.run(create_app(), host=host, port=port)
