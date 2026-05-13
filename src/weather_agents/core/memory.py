"""Memory system: short-term context, long-term persistence, working memory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from weather_agents.core.config import MemoryConfig


@dataclass
class Message:
    role: str  # "user", "assistant", "system", "tool"
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class Memory:
    """Three-layer memory for each agent."""

    def __init__(self, config: MemoryConfig, agent_name: str) -> None:
        self.config = config
        self.agent_name = agent_name
        self.short_term: list[Message] = []
        self.working: dict[str, Any] = {}
        self._db_path = Path(config.db_path).expanduser()
        self._db: aiosqlite.Connection | None = None

    async def init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_key ON memories(agent, key)"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # -- Short-term memory (conversation context) --

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        self.short_term.append(Message(role=role, content=content, **kwargs))
        if len(self.short_term) > self.config.short_term_limit:
            # Keep system messages + recent messages
            system_msgs = [m for m in self.short_term if m.role == "system"]
            other_msgs = [m for m in self.short_term if m.role != "system"]
            keep = self.config.short_term_limit - len(system_msgs)
            self.short_term = system_msgs + other_msgs[-keep:]

    def get_messages(self) -> list[dict]:
        msgs = []
        for m in self.short_term:
            d: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.name:
                d["name"] = m.name
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            msgs.append(d)
        return msgs

    def clear_short_term(self) -> None:
        system_msgs = [m for m in self.short_term if m.role == "system"]
        self.short_term = system_msgs

    # -- Working memory (task-scoped) --

    def set_working(self, key: str, value: Any) -> None:
        self.working[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        return self.working.get(key, default)

    def clear_working(self) -> None:
        self.working.clear()

    # -- Long-term memory (persistent) --

    async def remember(self, key: str, value: Any) -> None:
        if not self._db:
            return
        await self._db.execute(
            "INSERT OR REPLACE INTO memories (agent, key, value) VALUES (?, ?, ?)",
            (self.agent_name, key, json.dumps(value, ensure_ascii=False)),
        )
        await self._db.commit()

    async def recall(self, key: str | None = None, limit: int = 20) -> list[dict]:
        if not self._db:
            return []
        if key:
            cursor = await self._db.execute(
                "SELECT key, value FROM memories WHERE agent = ? AND key LIKE ? ORDER BY created_at DESC LIMIT ?",
                (self.agent_name, f"%{key}%", limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT key, value FROM memories WHERE agent = ? ORDER BY created_at DESC LIMIT ?",
                (self.agent_name, limit),
            )
        rows = await cursor.fetchall()
        return [{"key": r[0], "value": json.loads(r[1])} for r in rows]

    async def forget(self, key: str) -> None:
        if not self._db:
            return
        await self._db.execute(
            "DELETE FROM memories WHERE agent = ? AND key = ?",
            (self.agent_name, key),
        )
        await self._db.commit()
