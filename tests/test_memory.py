"""Tests for the memory system."""

from __future__ import annotations

import pytest

from weather_agents.core.config import MemoryConfig
from weather_agents.core.memory import Memory, Message


@pytest.fixture
def memory_config(tmp_path):
    return MemoryConfig(db_path=str(tmp_path / "test_memory.db"), short_term_limit=10)


@pytest.fixture
async def mem(memory_config):
    m = Memory(memory_config, "test_agent")
    await m.init_db()
    yield m
    await m.close()


class TestShortTermMemory:
    @pytest.mark.asyncio
    async def test_add_and_get_messages(self, mem):
        mem.add_message("user", "hello")
        mem.add_message("assistant", "hi there")
        msgs = mem.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["content"] == "hi there"

    @pytest.mark.asyncio
    async def test_system_message_preserved_on_truncation(self, mem):
        mem.add_message("system", "you are an agent")
        for i in range(15):
            mem.add_message("user", f"msg {i}")
        # System message should be preserved
        msgs = mem.get_messages()
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "you are an agent"

    @pytest.mark.asyncio
    async def test_clear_short_term(self, mem):
        mem.add_message("system", "system prompt")
        mem.add_message("user", "hello")
        mem.add_message("assistant", "hi")

        await mem.clear_short_term()
        msgs = mem.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_context_window_usage(self, mem):
        mem.add_message("user", "hello world")
        stats = mem.get_context_window_usage()
        assert stats["message_count"] == 1
        assert stats["total_chars"] == 11
        assert stats["limit"] == 10

    @pytest.mark.asyncio
    async def test_tool_message(self, mem):
        mem.add_message("tool", "file contents", name="read_file", tool_call_id="tc_123")
        msgs = mem.get_messages()
        assert msgs[0]["name"] == "read_file"
        assert msgs[0]["tool_call_id"] == "tc_123"


class TestWorkingMemory:
    @pytest.mark.asyncio
    async def test_set_get_working(self, mem):
        mem.set_working("task", {"id": "1"})
        assert mem.get_working("task") == {"id": "1"}
        assert mem.get_working("nonexistent", "default") == "default"

    @pytest.mark.asyncio
    async def test_clear_working(self, mem):
        mem.set_working("key", "value")
        mem.clear_working()
        assert mem.get_working("key") is None


class TestLongTermMemory:
    @pytest.mark.asyncio
    async def test_remember_and_recall(self, mem):
        await mem.remember("user_preference", {"theme": "dark"})
        results = await mem.recall("user_preference")
        assert len(results) == 1
        assert results[0]["value"] == {"theme": "dark"}

    @pytest.mark.asyncio
    async def test_remember_with_category(self, mem):
        await mem.remember("api_endpoint", "https://api.example.com", category="config")
        results = await mem.recall(category="config")
        assert len(results) == 1
        assert results[0]["category"] == "config"

    @pytest.mark.asyncio
    async def test_remember_updates_existing(self, mem):
        await mem.remember("key", "value1")
        await mem.remember("key", "value2")
        results = await mem.recall("key")
        assert len(results) == 1
        assert results[0]["value"] == "value2"

    @pytest.mark.asyncio
    async def test_forget(self, mem):
        await mem.remember("temp", "data")
        await mem.forget("temp")
        results = await mem.recall("temp")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_memory_stats(self, mem):
        await mem.remember("k1", "v1", category="config")
        await mem.remember("k2", "v2", category="config")
        await mem.remember("k3", "v3", category="notes")

        stats = await mem.get_memory_stats()
        assert stats["total"] == 3
        assert stats["categories"]["config"] == 2
        assert stats["categories"]["notes"] == 1

    @pytest.mark.asyncio
    async def test_recall_with_limit(self, mem):
        for i in range(10):
            await mem.remember(f"item_{i}", f"value_{i}")
        results = await mem.recall(limit=3)
        assert len(results) == 3


class TestMessageDataclass:
    def test_message_defaults(self):
        msg = Message(role="user", content="hello")
        assert msg.name is None
        assert msg.tool_call_id is None

    def test_message_with_tool_info(self):
        msg = Message(role="tool", content="result", name="read_file", tool_call_id="tc_1")
        assert msg.name == "read_file"
