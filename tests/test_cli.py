"""Tests for CLI interactive mode, slash commands, and streaming."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from typer.testing import CliRunner

from weather_agents.cli.main import app

runner = CliRunner()


class TestCLIFlags:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "Weather Agents" in result.stdout

    def test_help_output(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "chat" in result.stdout


class TestSlashCommandRouting:
    @pytest.mark.asyncio
    async def test_help_command(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/help", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_help") as mock_help:
                await _run_interactive("fog")
                mock_help.assert_called()

    @pytest.mark.asyncio
    async def test_clear_command(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/clear", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main.console.clear") as mock_clear:
                await _run_interactive("fog")
                mock_clear.assert_called()

    @pytest.mark.asyncio
    async def test_status_command(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/status", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_status") as mock_status:
                await _run_interactive("fog")
                mock_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_skills_command(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/skills", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_skills") as mock_skills:
                await _run_interactive("fog")
                mock_skills.assert_called_once()

    @pytest.mark.asyncio
    async def test_version_slash_command(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/version", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main.console.print") as mock_print:
                await _run_interactive("fog")
                # Should print version info
                assert any("Weather Agents" in str(c) for c in mock_print.call_args_list if c.args)


class TestAgentSwitching:
    @pytest.mark.asyncio
    async def test_switch_to_rain(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/rain", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("fog")
            # Should not crash

    @pytest.mark.asyncio
    async def test_switch_all_agents(self):
        for agent_name in ("fog", "rain", "frost", "snow", "dew"):
            with (
                patch("weather_agents.cli.main.create_system_context") as mock_create,
                patch(
                    "weather_agents.cli.main.console.input", side_effect=[f"/{agent_name}", "/quit"]
                ),
            ):
                mock_ctx = _make_ctx()
                mock_create.return_value = mock_ctx
                await _run_interactive("fog")
                # Should not crash for any agent switch

    @pytest.mark.asyncio
    async def test_switch_to_invalid_shows_help(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/unknown", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_help") as mock_help:
                await _run_interactive("fog")
                mock_help.assert_called()


class TestTaskCommand:
    @pytest.mark.asyncio
    async def test_task_invokes_run_task(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch(
                "weather_agents.cli.main.console.input",
                side_effect=["/task build a website", "/quit"],
            ),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._run_task") as mock_run:
                await _run_interactive("fog")
                mock_run.assert_called_once_with("build a website", mock_ctx.agent_map)

    @pytest.mark.asyncio
    async def test_task_empty_goal_ignored(self):
        with (
            patch("weather_agents.cli.main.console.input", side_effect=["/task ", "/quit"]),
            patch("weather_agents.cli.main._run_task") as mock_run,
            patch("weather_agents.cli.main.create_system_context") as mock_create,
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("fog")
            mock_run.assert_not_called()


class TestMemoryCommands:
    @pytest.mark.asyncio
    async def test_memory_status(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/memory", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_memory_status") as mock_mem:
                await _run_interactive("fog")
                mock_mem.assert_called_once()

    @pytest.mark.asyncio
    async def test_memory_clear(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/memory clear", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("fog")
            # Each agent's memory.clear_short_term should be awaited
            for ag in mock_ctx.agent_map.values():
                ag.memory.clear_short_term.assert_awaited()


class TestCostCommands:
    @pytest.mark.asyncio
    async def test_cost_display(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/cost", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_cost") as mock_cost:
                await _run_interactive("fog")
                mock_cost.assert_called_once()

    @pytest.mark.asyncio
    async def test_cost_reset(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/cost reset", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("fog")
            mock_ctx.llm.reset_usage_stats.assert_called_once()


class TestHistoryCommand:
    @pytest.mark.asyncio
    async def test_history(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/history", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_history") as mock_hist:
                await _run_interactive("fog")
                mock_hist.assert_called_once()


class TestMCPCommand:
    @pytest.mark.asyncio
    async def test_mcp_status(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/mcp", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_mcp_status") as mock_mcp:
                await _run_interactive("fog")
                mock_mcp.assert_called_once()


class TestModelCommand:
    @pytest.mark.asyncio
    async def test_model_list(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/model list", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._handle_model_command") as mock_model:
                await _run_interactive("fog")
                mock_model.assert_called_once()


class TestSkillActivation:
    @pytest.mark.asyncio
    async def test_use_skill(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch(
                "weather_agents.cli.main.console.input", side_effect=["/use code_reviewer", "/quit"]
            ),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("frost")
            mock_ctx.agent_map["frost"].activate_skill.assert_called_once_with("code_reviewer")

    @pytest.mark.asyncio
    async def test_use_unknown_skill(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/use badskill", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_ctx.agent_map["frost"].activate_skill.return_value = False
            mock_create.return_value = mock_ctx
            await _run_interactive("frost")
            mock_ctx.agent_map["frost"].activate_skill.assert_called_once_with("badskill")

    @pytest.mark.asyncio
    async def test_deactivate_skills(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/deactivate", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("frost")
            mock_ctx.agent_map["frost"].deactivate_all_skills.assert_called_once()


class TestExitCommands:
    @pytest.mark.asyncio
    async def test_all_exit_commands(self):
        for cmd in ("/quit", "/exit", "/q"):
            with (
                patch("weather_agents.cli.main.create_system_context") as mock_create,
                patch("weather_agents.cli.main.console.input", side_effect=[cmd]),
            ):
                mock_ctx = _make_ctx()
                mock_create.return_value = mock_ctx
                await _run_interactive("fog")
                # Should exit cleanly

    @pytest.mark.asyncio
    async def test_empty_input_continues(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["", "  ", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("fog")
            # Empty inputs should be ignored

    @pytest.mark.asyncio
    async def test_slash_without_match_shows_help(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["/nonexistent", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            with patch("weather_agents.cli.main._print_help") as mock_help:
                await _run_interactive("fog")
                mock_help.assert_called()


class TestKeyboardInterrupt:
    @pytest.mark.asyncio
    async def test_ctrl_c_exits(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=KeyboardInterrupt),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("fog")

    @pytest.mark.asyncio
    async def test_eof_exits(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=EOFError),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            await _run_interactive("fog")


class TestStreamingChat:
    @pytest.mark.asyncio
    async def test_streaming_content_rendered(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["hello", "/quit"]),
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx

            mock_ctx.agent_map["fog"].chat_stream = lambda _msg: _async_iter(
                [
                    {"type": "content", "text": "Hello"},
                    {"type": "content", "text": " world!"},
                    {"type": "done"},
                ]
            )

            with patch("weather_agents.cli.main.Live") as mock_live_cls:
                mock_live = MagicMock()
                mock_live_cls.return_value = mock_live

                await _run_interactive("fog")
                mock_live.start.assert_called_once()
                # Content updates via Live
                update_calls = mock_live.update.call_args_list
                assert len(update_calls) >= 2

    @pytest.mark.asyncio
    async def test_streaming_tool_status(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["do x", "/quit"]),
            patch("weather_agents.cli.main.Live") as mock_live_cls,
        ):
            mock_ctx = _make_ctx()
            mock_create.return_value = mock_ctx
            mock_live = MagicMock()
            mock_live_cls.return_value = mock_live

            mock_ctx.agent_map["fog"].chat_stream = lambda _msg: _async_iter(
                [
                    {"type": "content", "text": "Checking..."},
                    {"type": "tool_status", "label": "read_file /tmp/x"},
                    {"type": "content", "text": " Done."},
                    {"type": "done"},
                ]
            )

            await _run_interactive("fog")
            mock_live.start.assert_called_once()
            # Tool status included in updates
            update_calls = mock_live.update.call_args_list
            assert len(update_calls) >= 3

    @pytest.mark.asyncio
    async def test_streaming_interrupted(self):
        with (
            patch("weather_agents.cli.main.create_system_context") as mock_create,
            patch("weather_agents.cli.main.console.input", side_effect=["long query", "/quit"]),
        ):
            mock_ctx = _make_ctx()

            async def _raise_interrupt(_msg):
                raise KeyboardInterrupt
                yield  # makes it an async generator

            mock_ctx.agent_map["fog"].chat_stream = _raise_interrupt
            mock_create.return_value = mock_ctx
            # Should handle interrupt gracefully
            await _run_interactive("fog")


class TestRunTask:
    @pytest.mark.asyncio
    async def test_run_task_uses_orchestrate(self):
        """_run_task should delegate to factory.orchestrate_task, not duplicate logic."""
        from weather_agents.cli.main import _run_task

        mock_ctx = _make_ctx()

        with patch("weather_agents.core.factory.orchestrate_task") as mock_orch:
            mock_orch.return_value = ([], [], "no tasks")
            await _run_task("build x", agents=mock_ctx.agent_map)
            mock_orch.assert_called_once()
            assert mock_orch.call_args[0][0] == "build x"


# ── Helpers ────────────────────────────────────────────────────────────


def _make_ctx():
    """Create a minimal SystemContext mock."""
    ctx = Mock()
    ctx.config.llm.default_model = "deepseek-v4"
    ctx.agent_map = _make_agent_map()
    ctx.llm = Mock()
    ctx.llm.reset_usage_stats = Mock()
    ctx.llm.get_usage_stats = Mock(return_value={})
    ctx.llm.get_total_cost = Mock(return_value=0.0)
    ctx.init_all = AsyncMock()
    ctx.close_all = AsyncMock()
    ctx.mcp_status = []
    ctx.bus = Mock()
    ctx.bus.get_history = Mock(return_value=[])
    return ctx


def _make_agent_map():
    """Create a minimal agent map with mocks for all five agents."""
    from weather_agents.core.agent import AgentState

    agents = {}
    for name in ("fog", "rain", "frost", "snow", "dew"):
        ag = Mock()
        ag.name = name
        ag.display_name = {"fog": "雾", "rain": "雨", "frost": "霜", "snow": "雪", "dew": "露"}[
            name
        ]
        ag.emoji = {"fog": "🌫️", "rain": "🌧️", "frost": "❄️", "snow": "🌨️", "dew": "💧"}[name]
        ag.state = AgentState.IDLE
        ag.chat = AsyncMock(return_value="mock response")
        ag.chat_stream = lambda _msg, _ag=ag: _async_iter(
            [
                {"type": "content", "text": f"mock response from {_ag.name}"},
                {"type": "done"},
            ]
        )
        ag.activate_skill = Mock(return_value=True)
        ag.deactivate_all_skills = Mock()
        ag._active_skills = set()
        ag._skills = []
        ag.get_available_skills = Mock(return_value=[])
        ag.get_status = Mock(
            return_value={
                "name": name,
                "display_name": ag.display_name,
                "emoji": ag.emoji,
                "specialty": "test",
            }
        )
        ag.memory = Mock()
        ag.memory.short_term = []
        ag.memory.clear_short_term = AsyncMock()
        ag.memory.get_stats = Mock(
            return_value={
                "short_term_count": 0,
                "working_count": 0,
                "long_term_count": 0,
            }
        )
        agents[name] = ag
    return agents


async def _run_interactive(agent_name: str = "fog"):
    from weather_agents.cli.main import _interactive

    await _interactive(agent_name)


def _async_iter(events):
    """Return an awaitable that acts as an async iterator yielding events."""

    async def _gen():
        for e in events:
            yield e

    return _gen()
