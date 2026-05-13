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
    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class Memory:
    """Three-layer memory for each agent with SQLite persistence.

    - Short-term: conversation context (persisted to SQLite)
    - Working: in-memory task-scoped state
    - Long-term: persistent key-value storage with search
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
                category TEXT DEFAULT 'general',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Migrate existing DBs that lack the category/updated_at columns
        try:
            await self._db.execute(
                "ALTER TABLE memories ADD COLUMN category TEXT DEFAULT 'general'"
            )
        except Exception:
            pass
        try:
            await self._db.execute(
                "ALTER TABLE memories ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            )
        except Exception:
            pass
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_key ON memories(agent, key)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_category ON memories(agent, category)"
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
        if not self._db or self._loaded:
            return
        cursor = await self._db.execute(
            "SELECT role, content, name, tool_call_id FROM messages "
            "WHERE agent = ? ORDER BY created_at DESC LIMIT ?",
            (self.agent_name, self.config.short_term_limit),
        )
        rows = await cursor.fetchall()
        for row in reversed(rows):
            self.short_term.append(
                Message(role=row[0], content=row[1], name=row[2], tool_call_id=row[3])
            )
        self._loaded = True

    async def _flush_pending(self) -> None:
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
        msg = Message(role=role, content=content)
        if "name" in kwargs:
            msg.name = kwargs["name"]
        if "tool_call_id" in kwargs:
            msg.tool_call_id = kwargs["tool_call_id"]
        self.short_term.append(msg)

        if len(self.short_term) > self.config.short_term_limit:
            system_msgs = [m for m in self.short_term if m.role == "system"]
            other_msgs = [m for m in self.short_term if m.role != "system"]
            keep = self.config.short_term_limit - len(system_msgs)
            self.short_term = system_msgs + other_msgs[-keep:]

        if self._db and role != "system":
            task = asyncio.ensure_future(
                self._persist_message(role, content, msg.name, msg.tool_call_id)
            )
            self._pending_persists.add(task)
            task.add_done_callback(self._pending_persists.discard)

    async def _persist_message(
        self, role: str, content: str, name: str | None, tool_call_id: str | None,
    ) -> None:
        if not self._db:
            return
        try:
            await self._db.execute(
                "INSERT INTO messages (agent, role, content, name, tool_call_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.agent_name, role, content, name, tool_call_id),
            )
            await self._db.commit()
        except Exception:
            pass

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

    def get_context_window_usage(self) -> dict:
        """Return stats about current memory usage."""
        total_chars = sum(len(m.content) for m in self.short_term)
        return {
            "message_count": len(self.short_term),
            "total_chars": total_chars,
            "estimated_tokens": total_chars // 4,
            "limit": self.config.short_term_limit,
        }

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

    # -- Long-term memory (persistent key-value with categories) --

    async def remember(self, key: str, value: Any, category: str = "general") -> None:
        if not self._db:
            return
        existing = await self._db.execute(
            "SELECT id FROM memories WHERE agent = ? AND key = ?",
            (self.agent_name, key),
        )
        row = await existing.fetchone()
        if row:
            await self._db.execute(
                "UPDATE memories SET value = ?, category = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE agent = ? AND key = ?",
                (json.dumps(value, ensure_ascii=False), category, self.agent_name, key),
            )
        else:
            await self._db.execute(
                "INSERT INTO memories (agent, key, value, category) VALUES (?, ?, ?, ?)",
                (self.agent_name, key, json.dumps(value, ensure_ascii=False), category),
            )
        await self._db.commit()

    async def recall(
        self,
        key: str | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        if not self._db:
            return []

        query = "SELECT key, value, category FROM memories WHERE agent = ?"
        params: list[Any] = [self.agent_name]

        if key:
            query += " AND key LIKE ?"
            params.append(f"%{key}%")
        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {"key": r[0], "value": json.loads(r[1]), "category": r[2]}
            for r in rows
        ]

    async def forget(self, key: str) -> None:
        if not self._db:
            return
        await self._db.execute(
            "DELETE FROM memories WHERE agent = ? AND key = ?",
            (self.agent_name, key),
        )
        await self._db.commit()

    async def get_memory_stats(self) -> dict:
        """Return statistics about long-term memory."""
        if not self._db:
            return {"total": 0, "categories": {}}
        cursor = await self._db.execute(
            "SELECT category, COUNT(*) FROM memories WHERE agent = ? GROUP BY category",
            (self.agent_name,),
        )
        rows = await cursor.fetchall()
        categories = {r[0]: r[1] for r in rows}
        return {"total": sum(categories.values()), "categories": categories}
