"""Tests for the event bus."""

from __future__ import annotations

import pytest

from weather_agents.core.bus import Event, EventType


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self, bus):
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe("test_agent", handler)
        event = Event(type=EventType.SYSTEM_EVENT, source="system", data={"msg": "hello"})

        await bus.publish(event)

        assert len(received) == 1
        assert received[0].type == EventType.SYSTEM_EVENT
        assert received[0].data == {"msg": "hello"}

    @pytest.mark.asyncio
    async def test_direct_message(self, bus):
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe("target_agent", handler)
        event = Event(
            type=EventType.TASK_ASSIGNED,
            source="snow",
            target="target_agent",
            data={"task": "test"},
        )

        await bus.publish(event)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus):
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe("agent", handler)
        bus.unsubscribe("agent")

        await bus.publish(Event(type=EventType.SYSTEM_EVENT, source="system"))

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_state_listener(self, bus):
        received = []

        async def listener(event: Event):
            received.append(event)

        bus.on_state_change(listener)
        await bus.notify_state_change(
            Event(
                type=EventType.STATE_CHANGE,
                source="fog",
                data={"old_state": "idle", "new_state": "thinking"},
            )
        )

        assert len(received) == 1
        assert received[0].type == EventType.STATE_CHANGE
        assert received[0].source == "fog"

    @pytest.mark.asyncio
    async def test_history(self, bus):
        for i in range(5):
            await bus.publish(
                Event(
                    type=EventType.SYSTEM_EVENT,
                    source="system",
                    data={"i": i},
                )
            )

        assert len(bus.get_history()) == 5
        assert len(bus.get_history(limit=2)) == 2

    @pytest.mark.asyncio
    async def test_history_filter_by_agent(self, bus):
        await bus.publish(Event(type=EventType.SYSTEM_EVENT, source="fog"))
        await bus.publish(Event(type=EventType.SYSTEM_EVENT, source="rain"))

        fog_events = bus.get_history(agent_name="fog")
        assert len(fog_events) == 1
        assert fog_events[0].source == "fog"

    @pytest.mark.asyncio
    async def test_remove_state_listener(self, bus):
        received = []

        async def listener(event: Event):
            received.append(event)

        bus.on_state_change(listener)
        bus.remove_state_listener(listener)
        await bus.notify_state_change(Event(type=EventType.STATE_CHANGE, source="fog", data={}))

        assert len(received) == 0
