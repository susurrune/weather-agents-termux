"""FastAPI web application for Weather Agents dashboard."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from weather_agents.core.config import load_config
from weather_agents.core.bus import MessageBus, Event, EventType
from weather_agents.core.llm import LLMClient
from weather_agents.core.tool import global_registry
from weather_agents.core.mcp import MCPManager
from weather_agents.tools.builtin import register_builtin_tools
from weather_agents.agents.fog import FogAgent
from weather_agents.agents.rain import RainAgent
from weather_agents.agents.frost import FrostAgent
from weather_agents.agents.snow import SnowAgent
from weather_agents.agents.dew import DewAgent
from weather_agents.core.agent import BaseAgent


AGENT_MAP = {
    "fog": FogAgent,
    "rain": RainAgent,
    "frost": FrostAgent,
    "snow": SnowAgent,
    "dew": DewAgent,
}


class Session:
    """Isolated session with its own agents and bus."""

    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.config = load_config()
        self.bus = MessageBus()
        self.llm = LLMClient(self.config, global_registry)
        self.agents: dict[str, BaseAgent] = {}
        self._task = None

    async def init(self) -> None:
        for name, cls in AGENT_MAP.items():
            agent = cls(config=self.config, llm=self.llm, bus=self.bus, tool_registry=global_registry)
            self.agents[name] = agent
            await agent.init()

    async def close(self) -> None:
        for agent in self.agents.values():
            await agent.close()


class SessionManager:
    """Manages multiple user sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def get_or_create(self, session_id: str | None = None) -> Session:
        sid = session_id or uuid.uuid4().hex[:12]
        if sid not in self._sessions:
            session = Session(sid)
            await session.init()
            self._sessions[sid] = session
        return self._sessions[sid]

    async def remove(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            await session.close()

    async def cleanup_stale(self) -> None:
        """Remove all sessions (called on shutdown)."""
        for sid in list(self._sessions.keys()):
            await self.remove(sid)


# Global instances
session_manager = SessionManager()


def create_app() -> FastAPI:
    app = FastAPI(title="Weather Agents", version="0.2.0")

    @app.on_event("startup")
    async def startup():
        register_builtin_tools()
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

    # API routes
    @app.get("/api/agents")
    async def list_agents(session_id: str = ""):
        session = await session_manager.get_or_create(session_id or None)
        return [agent.get_status() for agent in session.agents.values()]

    @app.get("/api/agents/{agent_name}")
    async def get_agent(agent_name: str, session_id: str = ""):
        session = await session_manager.get_or_create(session_id or None)
        agent = session.agents.get(agent_name)
        if not agent:
            return {"error": "Agent not found"}
        return agent.get_status()

    @app.post("/api/agents/{agent_name}/chat")
    async def chat_with_agent(agent_name: str, body: dict, session_id: str = ""):
        session = await session_manager.get_or_create(session_id or None)
        agent = session.agents.get(agent_name)
        if not agent:
            return {"error": "Agent not found"}
        message = body.get("message", "")
        response = await agent.chat(message)
        return {"agent": agent_name, "response": response, "session_id": session.id}

    @app.post("/api/task")
    async def orchestrate_task(body: dict, session_id: str = ""):
        session = await session_manager.get_or_create(session_id or None)
        goal = body.get("goal", "")
        snow = session.agents.get("snow")
        if not snow:
            return {"error": "Snow agent not available"}
        tasks = await snow.orchestrate(goal)

        # Execute all assigned tasks
        results = []
        for task in tasks:
            assigned = task.assigned_to
            if not assigned or assigned == "snow":
                continue
            agent = session.agents.get(assigned)
            if not agent:
                continue
            from weather_agents.core.agent import Task as AgentTask
            a_task = AgentTask(
                id=task.id,
                description=task.description,
                assigned_to=assigned,
                metadata=task.metadata,
            )
            result = await agent.execute_task(a_task)
            task.result = result.content
            results.append({
                "id": task.id,
                "agent": assigned,
                "success": result.success,
                "content": result.content[:500],
            })

        # Generate summary
        if results:
            summary_prompt = "请汇总以下所有子任务的执行结果：\n\n"
            for r in results:
                status = "成功" if r["success"] else "失败"
                summary_prompt += f"## 任务 {r['id']} ({r['agent']}) - {status}\n"
                summary_prompt += f"{r['content'][:300]}\n\n"
            summary = await snow.chat(summary_prompt)
        else:
            summary = "没有需要执行的任务。"

        return {
            "goal": goal,
            "tasks": [
                {
                    "id": t.id,
                    "description": t.description,
                    "agent": t.assigned_to,
                    "depends_on": t.parent_id,
                }
                for t in tasks
            ],
            "results": results,
            "summary": summary,
        }

    @app.get("/api/history/{agent_name}")
    async def get_history(agent_name: str, limit: int = 20, session_id: str = ""):
        session = await session_manager.get_or_create(session_id or None)
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

    @app.get("/api/session")
    async def create_session():
        session = await session_manager.get_or_create()
        return {"session_id": session.id}

    @app.delete("/api/session/{session_id}")
    async def delete_session(session_id: str):
        await session_manager.remove(session_id)
        return {"status": "deleted", "session_id": session_id}

    @app.get("/api/config")
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
