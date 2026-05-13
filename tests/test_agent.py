"""Tests for base agent class."""

from __future__ import annotations


import pytest

from weather_agents.core.agent import AgentState, Task, TaskResult


class TestBaseAgent:
    def test_task_dataclass(self):
        task = Task(id="1", description="test task", assigned_to="fog")
        assert task.id == "1"
        assert task.status == "pending"
        assert task.assigned_to == "fog"

    def test_task_result_dataclass(self):
        r = TaskResult(success=True, content="done", data={"key": "val"})
        assert r.success is True
        assert r.content == "done"
        assert r.data["key"] == "val"

    def test_agent_states(self):
        assert AgentState.IDLE.value == "idle"
        assert AgentState.THINKING.value == "thinking"
        assert AgentState.ACTING.value == "acting"
        assert AgentState.ERROR.value == "error"

    @pytest.mark.asyncio
    async def test_concrete_agent_init(self, app_config, mock_llm, bus, tool_registry):
        """Verify a concrete FogAgent can init without error."""
        from weather_agents.agents.fog import FogAgent
        agent = FogAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)
        await agent.init()
        assert agent.name == "fog"
        assert agent.state == AgentState.IDLE
        assert agent.display_name == "雾"
        await agent.close()

    @pytest.mark.asyncio
    async def test_chat_returns_response(self, app_config, mock_llm, bus, tool_registry):
        from weather_agents.agents.fog import FogAgent
        agent = FogAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)
        await agent.init()
        response = await agent.chat("hello")
        assert response == "test response"
        await agent.close()

    @pytest.mark.asyncio
    async def test_execute_task(self, app_config, mock_llm, bus, tool_registry):
        from weather_agents.agents.rain import RainAgent
        agent = RainAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)
        await agent.init()
        task = Task(id="1", description="write code", assigned_to="rain")
        result = await agent.execute_task(task)
        assert result.success is True
        assert result.content == "test response"
        await agent.close()

    @pytest.mark.asyncio
    async def test_get_status(self, app_config, mock_llm, bus, tool_registry):
        from weather_agents.agents.fog import FogAgent
        agent = FogAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)
        await agent.init()
        status = agent.get_status()
        assert status["name"] == "fog"
        assert status["state"] == "idle"
        await agent.close()

    @pytest.mark.asyncio
    async def test_agent_has_system_prompt(self, app_config, mock_llm, bus, tool_registry):
        from weather_agents.agents.dew import DewAgent
        agent = DewAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)
        assert "露" in agent.system_prompt
        assert "运维" in agent.specialty
        await agent.close()

    def test_all_agent_classes_have_required_attrs(self):
        from weather_agents.agents.fog import FogAgent
        from weather_agents.agents.rain import RainAgent
        from weather_agents.agents.frost import FrostAgent
        from weather_agents.agents.snow import SnowAgent
        from weather_agents.agents.dew import DewAgent

        for cls in [FogAgent, RainAgent, FrostAgent, SnowAgent, DewAgent]:
            assert cls.name, f"{cls.__name__} missing name"
            assert cls.display_name, f"{cls.__name__} missing display_name"
            assert cls.emoji, f"{cls.__name__} missing emoji"
            assert cls.specialty, f"{cls.__name__} missing specialty"
            assert cls.system_prompt, f"{cls.__name__} missing system_prompt"
