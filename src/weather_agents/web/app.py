"""FastAPI web application for Weather Agents dashboard."""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from weather_agents.core.config import load_config
from weather_agents.core.bus import Event
from weather_agents.core.tool import global_registry
from weather_agents.core.mcp import MCPManager
from weather_agents.core.factory import create_system_context
from weather_agents.tools.builtin import register_builtin_tools

# Optional API token for production auth (set env WA_API_TOKEN)
_API_TOKEN: str | None = None


def _check_api_token(authorization: str | None = None) -> str | None:
    """Validate Bearer token if API auth is configured."""
    global _API_TOKEN
    if _API_TOKEN is None:
        _API_TOKEN = __import__("os").environ.get("WA_API_TOKEN", "")
    if not _API_TOKEN:
        return None  # no auth configured
    if not authorization:
        return "Missing Authorization header"
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != _API_TOKEN:
        return "Invalid API token"
    return None


class Session:
    """Isolated session with its own agents and bus."""

    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self._ctx = create_system_context()
        self.agents = self._ctx.agent_map
        self.bus = self._ctx.bus
        self.last_used: float = time.monotonic()

    async def init(self) -> None:
        await self._ctx.init_all()

    async def close(self) -> None:
        await self._ctx.close_all()

    def touch(self) -> None:
        self.last_used = time.monotonic()


class SessionManager:
    """Manages multiple user sessions with TTL and concurrency safety."""

    def __init__(self, session_ttl: int = 1800) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._session_ttl = session_ttl
        self._cleanup_task: asyncio.Task | None = None

    def start_cleanup(self, interval: int = 300) -> None:
        """Start background task that evicts expired sessions."""
        if self._cleanup_task is not None:
            return

        async def _evict_loop():
            while True:
                await asyncio.sleep(interval)
                await self._evict_expired()

        self._cleanup_task = asyncio.create_task(_evict_loop())

    async def stop_cleanup(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def _evict_expired(self) -> None:
        now = time.monotonic()
        async with self._lock:
            stale = [sid for sid, s in self._sessions.items() if now - s.last_used > self._session_ttl]
            for sid in stale:
                session = self._sessions.pop(sid)
                asyncio.ensure_future(session.close())

    async def get_or_create(self, session_id: str | None = None) -> Session:
        sid = session_id or uuid.uuid4().hex[:12]
        async with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                session = Session(sid)
                await session.init()
                self._sessions[sid] = session
            else:
                session.touch()
            return session

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            await session.close()

    async def cleanup_stale(self) -> None:
        """Remove all sessions (called on shutdown)."""
        await self.stop_cleanup()
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.close()

    async def get_session_count(self) -> int:
        async with self._lock:
            return len(self._sessions)


# Global instances
session_manager = SessionManager()


def create_app() -> FastAPI:
    app = FastAPI(title="Weather Agents", version="0.2.0")

    @app.on_event("startup")
    async def startup():
        register_builtin_tools()
        session_manager.start_cleanup()
        # Initialize MCP servers globally
        config = load_config()
        mcp_configs = getattr(config, "mcp", None)
        if mcp_configs and mcp_configs.get("servers"):
            mcp_manager = MCPManager(global_registry)
            mcp_manager.configure(mcp_configs["servers"])
            try:
                results = await mcp_manager.connect_all()
                for r in results:
                    print(f"MCP connected: {r}")
            except Exception:
                pass

    @app.on_event("shutdown")
    async def shutdown():
        await session_manager.cleanup_stale()

    # ── Auth dependency ─────────────────────────────────────────────────────
    from fastapi import Depends

    async def verify_token(authorization: str | None = Header(None)):
        err = _check_api_token(authorization)
        if err:
            raise HTTPException(status_code=401, detail=err)

    # API routes
    @app.get("/api/agents", dependencies=[Depends(verify_token)])
    async def list_agents(x_session_id: str | None = Header(None)):
        session = await session_manager.get_or_create(x_session_id)
        return [agent.get_status() for agent in session.agents.values()]

    @app.get("/api/agents/{agent_name}", dependencies=[Depends(verify_token)])
    async def get_agent(agent_name: str, x_session_id: str | None = Header(None)):
        session = await session_manager.get_or_create(x_session_id)
        agent = session.agents.get(agent_name)
        if not agent:
            return {"error": "Agent not found"}
        return agent.get_status()

    @app.post("/api/agents/{agent_name}/chat", dependencies=[Depends(verify_token)])
    async def chat_with_agent(agent_name: str, body: dict, x_session_id: str | None = Header(None)):
        session = await session_manager.get_or_create(x_session_id)
        agent = session.agents.get(agent_name)
        if not agent:
            return {"error": "Agent not found"}
        message = body.get("message", "")
        response = await agent.chat(message)
        return {"agent": agent_name, "response": response, "session_id": session.id}

    @app.post("/api/task", dependencies=[Depends(verify_token)])
    async def orchestrate_task(body: dict, x_session_id: str | None = Header(None)):
        session = await session_manager.get_or_create(x_session_id)
        from weather_agents.core.factory import orchestrate_task as run_orch
        tasks, results, summary = await run_orch(
            body.get("goal", ""),
            session.agents,
        )
        return {
            "goal": body.get("goal", ""),
            "tasks": [
                {
                    "id": t.id,
                    "description": t.description,
                    "agent": t.assigned_to,
                    "depends_on": t.parent_id,
                }
                for t in tasks
            ],
            "results": [
                {"id": r.id, "agent": r.agent, "success": r.success, "content": r.content}
                for r in results
            ],
            "summary": summary,
        }

    @app.get("/api/history/{agent_name}", dependencies=[Depends(verify_token)])
    async def get_history(agent_name: str, limit: int = 20, x_session_id: str | None = Header(None)):
        session = await session_manager.get_or_create(x_session_id)
        events = session.bus.get_history(agent_name=agent_name, limit=limit)
        return [
            {
                "type": e.type.value,
                "source": e.source,
                "target": e.target,
                "data": e.data,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in events
        ]

    @app.get("/api/session", dependencies=[Depends(verify_token)])
    async def create_session():
        session = await session_manager.get_or_create()
        return {"session_id": session.id}

    @app.delete("/api/session/{session_id}", dependencies=[Depends(verify_token)])
    async def delete_session(session_id: str):
        await session_manager.remove(session_id)
        return {"status": "deleted", "session_id": session_id}

    @app.get("/api/config", dependencies=[Depends(verify_token)])
    async def get_config():
        cfg = load_config()
        return {
            "default_model": cfg.llm.default_model,
            "temperature": cfg.llm.temperature,
            "max_tokens": cfg.llm.max_tokens,
            "timeout": cfg.llm.timeout,
            "api_keys": list(cfg.llm.api_keys.keys()),
            "agents": {
                name: {"model": getattr(cfg.agents, name).model or cfg.llm.default_model, "specialty": getattr(cfg.agents, name).specialty}
                for name in ("fog", "rain", "frost", "snow", "dew")
            },
        }

    # WebSocket
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        session_id = uuid.uuid4().hex[:12]
        session = await session_manager.get_or_create(session_id)

        # Subscribe to state changes
        async def on_state_change(event: Event) -> None:
            try:
                await ws.send_json({
                    "type": "state_change",
                    "agent": event.source,
                    "state": event.data.get("new_state", ""),
                    "old_state": event.data.get("old_state", ""),
                })
            except Exception:
                pass

        session.bus.on_state_change(on_state_change)

        try:
            await ws.send_json({
                "type": "session_ready",
                "session_id": session_id,
                "agents": [a.get_status() for a in session.agents.values()],
            })

            while True:
                data = await ws.receive_text()
                msg = json.loads(data)
                msg_type = msg.get("type")

                if msg_type == "chat":
                    agent_name = msg.get("agent", "fog")
                    agent = session.agents.get(agent_name)
                    if not agent:
                        await ws.send_json({"type": "error", "message": f"Unknown agent: {agent_name}"})
                        continue

                    # Stream response
                    full = ""
                    async for chunk in agent.chat_stream(msg.get("message", "")):
                        full += chunk
                        await ws.send_json({
                            "type": "chat_chunk",
                            "agent": agent_name,
                            "chunk": chunk,
                            "done": False,
                        })
                    await ws.send_json({
                        "type": "chat_chunk",
                        "agent": agent_name,
                        "chunk": "",
                        "done": True,
                        "full": full,
                    })

                elif msg_type == "task":
                    goal = msg.get("goal", "")
                    snow = session.agents.get("snow")
                    if snow:
                        tasks = await snow.orchestrate(goal)
                        await ws.send_json({
                            "type": "task_plan",
                            "goal": goal,
                            "tasks": [
                                {"id": t.id, "description": t.description, "agent": t.assigned_to}
                                for t in tasks
                            ],
                        })

                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})

        except WebSocketDisconnect:
            pass
        finally:
            session.bus.remove_state_listener(on_state_change)

    # Serve static frontend
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(static_dir / "index.html")

    return app
