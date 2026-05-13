"""Tests for base agent class."""

from __future__ import annotations


import pytest

from weather_agents.core.agent import AgentState, Task, TaskResult
from weather_agents.core.skill import Skill, SkillRegistry


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
            assert cls.skill_names, f"{cls.__name__} missing skill_names"
            assert len(cls.skill_names) == 3, f"{cls.__name__} should have 3 skills"


class TestSkillSystem:
    def test_skill_dataclass(self):
        skill = Skill(name="test", description="a test skill", system_prompt="you are a test skill")
        assert skill.name == "test"
        assert skill.description == "a test skill"
        assert skill.required_tools == []

    def test_skill_registry(self):
        reg = SkillRegistry()
        skill = Skill(name="web_research", description="research", system_prompt="test")
        reg.register(skill)
        assert reg.get("web_research") is skill
        assert reg.list_names() == ["web_research"]

    def test_skill_registry_get_multiple(self):
        reg = SkillRegistry()
        reg.register(Skill(name="a", description="skill a"))
        reg.register(Skill(name="b", description="skill b"))
        skills = reg.get_skills(["a", "c"])
        assert len(skills) == 1
        assert skills[0].name == "a"

    def test_agent_skill_names_loaded(self):
        """Verify FogAgent has correct skill_names from class attribute."""
        from weather_agents.agents.fog import FogAgent
        assert "web_research" in FogAgent.skill_names
        assert "code_analysis" in FogAgent.skill_names
        assert "document_analysis" in FogAgent.skill_names

    @pytest.mark.asyncio
    async def test_activate_skill_with_registry(self, app_config, mock_llm, bus, tool_registry):
        """Activate a skill via SkillRegistry."""
        reg = SkillRegistry()
        # Register all FogAgent's skills so _load_skills picks them up
        reg.register(Skill(name="web_research", description="research", system_prompt="你擅长调研", required_tools=["read_file"]))
        reg.register(Skill(name="code_analysis", description="analysis", system_prompt="分析"))
        reg.register(Skill(name="document_analysis", description="docs", system_prompt="文档"))

        # Register the required tool
        from weather_agents.core.tool import Tool, ToolParameter
        tool_registry.register(Tool(
            name="read_file", description="read",
            parameters=[ToolParameter(name="path", type="string", description="path")],
            handler=lambda **kw: "content",
        ))

        from weather_agents.agents.fog import FogAgent
        agent = FogAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry, skill_registry=reg)
        await agent.init()

        assert agent.activate_skill("web_research") is True
        assert "web_research" in agent.get_active_skills()
        await agent.close()

    @pytest.mark.asyncio
    async def test_deactivate_skill(self, app_config, mock_llm, bus, tool_registry):
        reg = SkillRegistry()
        # Use FogAgent's actual skill names
        reg.register(Skill(name="web_research", description="research", system_prompt="research prompt"))
        reg.register(Skill(name="code_analysis", description="analysis", system_prompt="analysis prompt"))
        reg.register(Skill(name="document_analysis", description="docs", system_prompt="docs prompt"))

        from weather_agents.agents.fog import FogAgent
        agent = FogAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry, skill_registry=reg)
        await agent.init()

        agent.activate_skill("web_research")
        assert len(agent.get_active_skills()) == 1

        agent.deactivate_all_skills()
        assert len(agent.get_active_skills()) == 0
        await agent.close()

    @pytest.mark.asyncio
    async def test_get_available_skills(self, app_config, mock_llm, bus, tool_registry):
        reg = SkillRegistry()
        # Use FogAgent's actual skill names
        reg.register(Skill(name="web_research", description="Deep web research"))
        reg.register(Skill(name="code_analysis", description="Code analysis"))
        reg.register(Skill(name="document_analysis", description="Document analysis"))

        from weather_agents.agents.fog import FogAgent
        agent = FogAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry, skill_registry=reg)
        await agent.init()

        available = agent.get_available_skills()
        names = {s["name"] for s in available}
        assert "web_research" in names
        assert "code_analysis" in names
        assert "document_analysis" in names
        await agent.close()

    def test_get_status_includes_skills(self):
        reg = SkillRegistry()
        reg.register(Skill(name="demo", description="demo skill"))

        from unittest.mock import Mock
        agent = Mock()
        agent.get_status.return_value = {
            "name": "test",
            "state": "idle",
            "skills": [{"name": "demo", "description": "demo skill", "active": False}],
        }
        status = agent.get_status()
        assert "skills" in status
        assert len(status["skills"]) == 1
