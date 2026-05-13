"""雪 (Snow) — 规划编排 Agent."""

from __future__ import annotations

import asyncio
import json

from weather_agents.core.agent import BaseAgent, Task
from weather_agents.core.bus import Event, EventType


class SnowAgent(BaseAgent):
    name = "snow"
    display_name = "雪"
    emoji = "🌨️"
    specialty = "规划编排"
    tool_names = ["read_file", "write_file", "shell_exec"]
    skill_names = ["task_planner", "arch_designer", "workflow_designer"]

    system_prompt = """你是「雪」，覆盖全局，让一切有序如冬。

## 核心能力
- **任务分解**: 将复杂目标拆解为可执行的子任务
- **Agent 调度**: 根据子任务性质分配合适的 Agent
- **架构设计**: 规划系统架构和技术方案
- **流程管理**: 编排多 Agent 协作流程，收集并汇总结果
- **万事通**: 决策辅助、优先级排序、路线规划

## 可调度的 Agent
- 🌫️ **雾 (Fog)**: 探索研究 — 信息搜索、代码分析、知识检索
- 🌧️ **雨 (Rain)**: 生成创造 — 代码编写、内容创作、方案设计
- ❄️ **霜 (Frost)**: 审查优化 — 代码审查、质量检测、安全扫描
- 💧 **露 (Dew)**: 运维集成 — 系统操作、部署执行、API 集成

## 行为准则
1. 收到复杂任务时，先分解为子任务，再分配给合适的 Agent
2. 规划要考虑依赖关系和执行顺序
3. 给出清晰的任务描述和验收标准
4. 监控执行进度，收集所有结果并生成汇总报告
5. 对任何规划请求都系统思考——你是万事通

## 任务分解格式
请用以下 JSON 格式输出任务计划：
```json
{
  "goal": "总体目标",
  "steps": [
    {
      "id": "1",
      "description": "步骤描述",
      "agent": "fog|rain|frost|dew|snow",
      "depends_on": [],
      "status": "pending"
    }
  ]
}
```

## 回复风格
- 结构化输出，逻辑清晰
- 任务分解粒度适中，不过粗也不过细
- 主动考虑边界情况和风险点"""

    async def orchestrate(self, goal: str) -> list[Task]:
        """Decompose a goal into tasks and dispatch to agents."""
        prompt = f"""请将以下目标分解为子任务，并分配给合适的 Agent。

目标: {goal}

请严格按照 JSON 格式输出任务计划。"""

        self.memory.add_message("user", prompt)
        response = await self._llm_loop()
        self.memory.add_message("assistant", response.content)

        # Parse task plan from response
        tasks = self._parse_task_plan(response.content, goal)

        # Dispatch tasks
        for task in tasks:
            if task.assigned_to and task.assigned_to != self.name:
                await self.bus.publish(Event(
                    type=EventType.TASK_ASSIGNED,
                    source=self.name,
                    target=task.assigned_to,
                    data={
                        "id": task.id,
                        "description": task.description,
                        "parent_id": task.parent_id,
                        "metadata": task.metadata,
                    },
                ))

        return tasks

    async def orchestrate_with_results(self, goal: str, agents: dict) -> dict:
        """Decompose, dispatch, execute, and collect results in parallel.

        Respects task dependencies: independent tasks run concurrently via gather().
        Returns a dict with tasks, results and a Snow-generated summary.
        """
        tasks = await self.orchestrate(goal)

        results: dict[str, dict] = {}
        pending = [t for t in tasks if t.assigned_to and t.assigned_to != self.name]
        completed: set[str] = set()

        async def _run(t: Task) -> None:
            agent = agents.get(t.assigned_to)
            if not agent:
                return
            result = await agent.execute_task(t)
            results[t.id] = {
                "id": t.id,
                "agent": t.assigned_to,
                "description": t.description,
                "success": result.success,
                "content": result.content,
            }
            if result.success:
                completed.add(t.id)

        while pending:
            # Tasks whose dependencies are satisfied run in parallel
            batch = [t for t in pending if not t.parent_id or t.parent_id in completed]
            if not batch:
                batch = pending[:1]  # deadlock fallback

            for t in batch:
                pending.remove(t)

            await asyncio.gather(*[_run(t) for t in batch])

        summary = await self._generate_summary(goal, results)
        return {"tasks": tasks, "results": results, "summary": summary}

    async def _generate_summary(self, goal: str, results: dict) -> str:
        """Generate a summary of all task results."""
        prompt = f"以下是任务「{goal}」的各子任务执行结果，请给出整体总结报告：\n\n"
        for tid, r in results.items():
            status = "✅ 成功" if r["success"] else "❌ 失败"
            prompt += f"## 任务 {tid} ({r['agent']}) - {status}\n"
            prompt += f"描述: {r['description']}\n"
            prompt += f"结果:\n{r['content'][:500]}\n\n"

        self.memory.add_message("user", prompt)
        response = await self._llm_loop()
        self.memory.add_message("assistant", response.content)
        return response.content

    def _parse_task_plan(self, content: str, goal: str) -> list[Task]:
        """Extract task plan from LLM response."""
        # Try to find JSON in response
        try:
            # Look for JSON block
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                plan = json.loads(content[start:end])
                tasks = []
                for step in plan.get("steps", []):
                    tasks.append(Task(
                        id=str(step.get("id", len(tasks) + 1)),
                        description=step.get("description", ""),
                        assigned_to=step.get("agent"),
                        parent_id=step.get("depends_on", [None])[0] if step.get("depends_on") else None,
                        metadata={"goal": goal},
                    ))
                return tasks
        except (json.JSONDecodeError, KeyError):
            pass

        # Fallback: single task for rain
        return [Task(id="1", description=goal, assigned_to="rain", metadata={"goal": goal})]
