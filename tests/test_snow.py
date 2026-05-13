"""Tests for Snow orchestration agent."""

from __future__ import annotations

import pytest



class TestSnowParseTaskPlan:
    """Test the JSON parsing logic in SnowAgent._parse_task_plan."""

    @pytest.mark.asyncio
    async def test_parse_simple_json(self, app_config, mock_llm, bus, tool_registry):
        from weather_agents.agents.snow import SnowAgent
        agent = SnowAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)

        content = """{
            "goal": "test goal",
            "steps": [
                {"id": "1", "description": "step 1", "agent": "fog", "depends_on": []},
                {"id": "2", "description": "step 2", "agent": "rain", "depends_on": ["1"]}
            ]
        }"""
        tasks = agent._parse_task_plan(content, "test goal")
        assert len(tasks) == 2
        assert tasks[0].id == "1"
        assert tasks[0].description == "step 1"
        assert tasks[0].assigned_to == "fog"
        assert tasks[1].assigned_to == "rain"
        assert tasks[1].parent_id == "1"

    @pytest.mark.asyncio
    async def test_parse_invalid_json_fallback(self, app_config, mock_llm, bus, tool_registry):
        from weather_agents.agents.snow import SnowAgent
        agent = SnowAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)

        content = "I don't know how to do this, here's some text without JSON"
        tasks = agent._parse_task_plan(content, "fallback goal")
        assert len(tasks) == 1
        assert tasks[0].assigned_to == "rain"
        assert tasks[0].description == "fallback goal"

    @pytest.mark.asyncio
    async def test_parse_empty_steps_fallback(self, app_config, mock_llm, bus, tool_registry):
        from weather_agents.agents.snow import SnowAgent
        agent = SnowAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)

        content = '{"goal": "x", "steps": []}'
        tasks = agent._parse_task_plan(content, "empty goal")
        assert len(tasks) == 0

    @pytest.mark.asyncio
    async def test_parse_without_depends(self, app_config, mock_llm, bus, tool_registry):
        from weather_agents.agents.snow import SnowAgent
        agent = SnowAgent(config=app_config, llm=mock_llm, bus=bus, tool_registry=tool_registry)

        content = '{"steps": [{"id": "1", "agent": "dew"}]}'
        tasks = agent._parse_task_plan(content, "minimal")
        assert len(tasks) == 1
        assert tasks[0].assigned_to == "dew"
        assert tasks[0].parent_id is None
