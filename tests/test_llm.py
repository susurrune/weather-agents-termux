"""Tests for the LLM client."""

from __future__ import annotations

import pytest

from weather_agents.core.llm import LLMClient, LLMResponse, estimate_cost


class TestEstimateCost:
    def test_known_model(self):
        cost = estimate_cost("gpt-4o-mini", 1000, 500)
        assert cost > 0
        assert cost < 1.0

    def test_unknown_model_fallback(self):
        cost = estimate_cost("unknown-model", 1000, 500)
        assert cost > 0

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4o", 0, 0)
        assert cost == 0.0

    def test_local_model_free(self):
        cost = estimate_cost("ollama/llama3", 10000, 5000)
        assert cost == 0.0


class TestLLMResponse:
    def test_defaults(self):
        r = LLMResponse(content="hello")
        assert r.content == "hello"
        assert r.tool_calls == []
        assert r.model == ""
        assert r.cost == 0.0

    def test_with_tool_calls(self):
        r = LLMResponse(
            content="",
            tool_calls=[{"id": "1", "name": "read_file", "arguments": {"path": "/tmp"}}],
            model="gpt-4o",
        )
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0]["name"] == "read_file"


class TestLLMClientUsageTracking:
    @pytest.fixture
    def client(self, app_config, tool_registry):
        return LLMClient(app_config, tool_registry)

    def test_initial_stats_empty(self, client):
        assert client.get_usage_stats() == {}
        assert client.get_total_cost() == 0.0

    def test_track_usage(self, client):
        client._track_usage("fog", "gpt-4o-mini", 100, 50)
        stats = client.get_usage_stats()
        assert "fog" in stats
        assert stats["fog"]["calls"] == 1
        assert stats["fog"]["prompt_tokens"] == 100
        assert stats["fog"]["completion_tokens"] == 50
        assert stats["fog"]["cost"] > 0

    def test_cumulative_tracking(self, client):
        client._track_usage("fog", "gpt-4o-mini", 100, 50)
        client._track_usage("fog", "gpt-4o-mini", 200, 100)
        stats = client.get_usage_stats()
        assert stats["fog"]["calls"] == 2
        assert stats["fog"]["prompt_tokens"] == 300

    def test_multiple_agents(self, client):
        client._track_usage("fog", "gpt-4o", 100, 50)
        client._track_usage("rain", "gpt-4o-mini", 200, 100)
        stats = client.get_usage_stats()
        assert len(stats) == 2
        assert "fog" in stats
        assert "rain" in stats

    def test_reset_stats(self, client):
        client._track_usage("fog", "gpt-4o", 100, 50)
        client.reset_usage_stats()
        assert client.get_usage_stats() == {}
        assert client.get_total_cost() == 0.0

    def test_budget_check_passes(self, app_config, tool_registry):
        client = LLMClient(app_config, tool_registry, cost_limit=10.0)
        client._check_budget()  # should not raise

    def test_budget_check_fails(self, app_config, tool_registry):
        client = LLMClient(app_config, tool_registry, cost_limit=0.001)
        client._track_usage("fog", "gpt-4o", 10000, 5000)
        with pytest.raises(RuntimeError, match="Cost limit exceeded"):
            client._check_budget()
