"""Test fixtures and mocks for Weather Agents."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from weather_agents.core.bus import MessageBus
from weather_agents.core.tool import Tool, ToolRegistry


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def tool_registry():
    r = ToolRegistry()
    r.register(Tool(
        name="test_tool",
        description="A test tool",
        parameters=[],
        handler=AsyncMock(return_value="tool result"),
    ))
    return r


@pytest.fixture
def mock_llm():
    llm = Mock()
    llm.complete = AsyncMock(return_value=Mock(
        content="test response",
        tool_calls=[],
        model="gpt-4o-mini",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    ))
    llm.stream = AsyncMock()
    return llm


@pytest.fixture
def app_config():
    from weather_agents.core.config import AppConfig
    return AppConfig()
