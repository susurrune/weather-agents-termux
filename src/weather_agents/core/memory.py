"""Memory system: short-term context, long-term persistence, working memory."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
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
    """Three-layer memory for each agent with SQLite persistence.

    Short-term memory persists messages to SQLite so conversation history
    survives agent restart. Working memory is in-memory (task-scoped).
    Long-term memory uses key-value storage in SQLite.
    """

    def __init__(self, config: MemoryConfig, agent_name: str) -> None:
        self.config = config
        self.agent_name = agent_name
        self.short_term: list[Message] = []
        self.working: dict[str, Any] = {}
        self._db_path = Path(config.db_path).expanduser()
        self._db: aiosqlite.Connection | None = None
        self._loaded = False
        self._pending_persists: set[asyncio.Task] = set()

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
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                name TEXT,
                tool_call_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_agent ON messages(agent, created_at)"
        )
        await self._db.commit()
        await self._load_short_term()

    async def _load_short_term(self) -> None:
        """Load recent messages from SQLite into in-memory short_term."""
        if not self._db or self._loaded:
            return
        cursor = await self._db.execute(
            "SELECT role, content, name, tool_call_id FROM messages "
            "WHERE agent = ? ORDER BY created_at DESC LIMIT ?",
            (self.agent_name, self.config.short_term_limit),
        )
        rows = await cursor.fetchall()
        # Reverse to get chronological order
        for row in reversed(rows):
            self.short_term.append(
                Message(role=row[0], content=row[1], name=row[2], tool_call_id=row[3])
            )
        self._loaded = True

    async def _flush_pending(self) -> None:
        """Wait for any pending persist tasks to complete."""
        if self._pending_persists:
            await asyncio.gather(*self._pending_persists, return_exceptions=True)
            self._pending_persists.clear()
        if self._db:
            try:
                await self._db.commit()
            except Exception:
                pass

    async def close(self) -> None:
        if self._db:
            await self._flush_pending()
            await self._db.close()

    # -- Short-term memory (conversation context, persisted) --

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        self.short_term.append(Message(role=role, content=content, **kwargs))
        if len(self.short_term) > self.config.short_term_limit:
            system_msgs = [m for m in self.short_term if m.role == "system"]
            other_msgs = [m for m in self.short_term if m.role != "system"]
            keep = self.config.short_term_limit - len(system_msgs)
            self.short_term = system_msgs + other_msgs[-keep:]
        # Persist to SQLite (non-blocking fire-and-forget)
        if self._db and role != "system":
            name = kwargs.get("name")
            tool_call_id = kwargs.get("tool_call_id")
            task = asyncio.ensure_future(
                self._persist_message(role, content, name, tool_call_id)
            )
            self._pending_persists.add(task)
            task.add_done_callback(self._pending_persists.discard)

    async def _persist_message(
        self, role: str, content: str, name: str | None, tool_call_id: str | None
    ) -> None:
        if not self._db:
            return
        try:
            await self._db.execute(
                "INSERT INTO messages (agent, role, content, name, tool_call_id) VALUES (?, ?, ?, ?, ?)",
                (self.agent_name, role, content, name, tool_call_id),
            )
            await self._db.commit()
        except Exception:
            pass  # Persistence failures are non-critical

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

    async def clear_short_term(self) -> None:
        system_msgs = [m for m in self.short_term if m.role == "system"]
        self.short_term = system_msgs
        if self._db:
            await self._db.execute(
                "DELETE FROM messages WHERE agent = ? AND role != 'system'",
                (self.agent_name,),
            )
            await self._db.commit()

    # -- Working memory (task-scoped) --

    def set_working(self, key: str, value: Any) -> None:
        self.working[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        return self.working.get(key, default)

    def clear_working(self) -> None:
        self.working.clear()

    # -- Long-term memory (persistent key-value) --

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
