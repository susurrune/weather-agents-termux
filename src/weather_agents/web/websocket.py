"""WebSocket handler helpers for state broadcasting."""

from __future__ import annotations

from weather_agents.core.bus import Event, MessageBus


def subscribe_agent_states(bus: MessageBus, send_json_coro):
    """Subscribe to agent state changes and broadcast via send_json."""
    async def on_state_change(event: Event) -> None:
        try:
            await send_json_coro({
                "type": "state_change",
                "agent": event.source,
                "state": event.data.get("new_state", ""),
                "old_state": event.data.get("old_state", ""),
            })
        except Exception:
            pass

    bus.on_state_change(on_state_change)
    return on_state_change
