"""雪 (Snow) — 规划编排 Agent."""

from __future__ import annotations

import asyncio
import json
import re

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
      "priority": "high|medium|low"
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
        prompt = (
            f"请将以下目标分解为子任务，并分配给合适的 Agent。\n\n"
            f"目标: {goal}\n\n"
            f"请严格按照 JSON 格式输出任务计划。"
        )

        self.memory.add_message("user", prompt)
        response = await self._llm_loop()
        self.memory.add_message("assistant", response.content)

        tasks = self._parse_task_plan(response.content, goal)

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

    async def orchestrate_with_results(
        self,
        goal: str,
        agents: dict,
        max_iterations: int = 3,
    ) -> dict:
        """Decompose, dispatch, execute, and collect results with retry on failure."""
        all_tasks: list[Task] = []
        all_results: dict[str, dict] = {}
        context = goal
        iteration = 0

        for iteration in range(max_iterations):
            if iteration > 0:
                failed = [r for r in all_results.values() if not r["success"]]
                if not failed:
                    break
                context = self._build_refinement_prompt(goal, failed)

            tasks = await self.orchestrate(context)

            if iteration == 0:
                all_tasks = tasks
            else:
                all_tasks.extend(tasks)

            pending = [t for t in tasks if t.assigned_to and t.assigned_to != self.name]
            completed: set[str] = set()

            async def _run(t: Task) -> None:
                agent = agents.get(t.assigned_to)
                if not agent:
                    return
                result = await agent.execute_task(t)
                all_results[t.id] = {
                    "id": t.id,
                    "agent": t.assigned_to,
                    "description": t.description,
                    "success": result.success,
                    "content": result.content,
                }
                if result.success:
                    completed.add(t.id)

            # Execute with dependency ordering
            while pending:
                batch = [
                    t for t in pending
                    if not t.parent_id or t.parent_id in completed
                ]
                if not batch:
                    batch = pending[:1]
                for t in batch:
                    pending.remove(t)
                await asyncio.gather(*[_run(t) for t in batch])

        summary = await self._generate_summary(goal, all_results)
        return {
            "tasks": all_tasks,
            "results": all_results,
            "summary": summary,
            "iterations": iteration + 1,
        }

    def _build_refinement_prompt(self, goal: str, failed: list[dict]) -> str:
        parts = [f"原始目标: {goal}\n\n以下子任务执行失败，请重新规划：\n"]
        for f in failed:
            parts.append(f"- 任务 {f['id']} ({f['agent']}): {f['description']}")
            parts.append(f"  错误: {f['content'][:200]}")
        parts.append("\n请输出新的任务计划 JSON。")
        return "\n".join(parts)

    async def _generate_summary(self, goal: str, results: dict) -> str:
        prompt = f"以下是任务「{goal}」的各子任务执行结果，请给出整体总结报告：\n\n"
        for tid, r in results.items():
            status = "✅ 成功" if r["success"] else "❌ 失败"
            prompt += (
                f"## 任务 {tid} ({r['agent']}) - {status}\n"
                f"描述: {r['description']}\n"
                f"结果:\n{r['content'][:500]}\n\n"
            )

        self.memory.add_message("user", prompt)
        response = await self._llm_loop()
        self.memory.add_message("assistant", response.content)
        return response.content

    def _parse_task_plan(self, content: str, goal: str) -> list[Task]:
        """Extract task plan from LLM response with robust JSON parsing."""
        # Try to extract JSON from markdown code blocks first
        json_str = self._extract_json(content)
        if json_str:
            try:
                plan = json.loads(json_str)
                tasks = self._plan_to_tasks(plan, goal)
                if tasks is not None:
                    return tasks
            except (json.JSONDecodeError, KeyError):
                pass

        # Try to find raw JSON in response
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                plan = json.loads(content[start:end])
                tasks = self._plan_to_tasks(plan, goal)
                if tasks is not None:
                    return tasks
        except (json.JSONDecodeError, KeyError):
            pass

        # Fallback: single task for rain
        return [Task(id="1", description=goal, assigned_to="rain", metadata={"goal": goal})]

    @staticmethod
    def _extract_json(content: str) -> str | None:
        """Extract JSON from markdown code blocks."""
        patterns = [
            re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL),
            re.compile(r"```\s*\n(\{.*?\})\n```", re.DOTALL),
        ]
        for pattern in patterns:
            match = pattern.search(content)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _plan_to_tasks(plan: dict, goal: str) -> list[Task]:
        """Convert a parsed plan dict to Task objects."""
        valid_agents = {"fog", "rain", "frost", "snow", "dew"}
        tasks = []
        for step in plan.get("steps", []):
            agent = step.get("agent", "rain")
            if agent not in valid_agents:
                agent = "rain"
            depends = step.get("depends_on", [])
            parent_id = depends[0] if depends else None
            tasks.append(Task(
                id=str(step.get("id", len(tasks) + 1)),
                description=step.get("description", ""),
                assigned_to=agent,
                parent_id=parent_id,
                metadata={
                    "goal": goal,
                    "priority": step.get("priority", "medium"),
                },
            ))
        return tasks
