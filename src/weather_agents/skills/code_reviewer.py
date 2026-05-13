"""Skill: Code Reviewer — systematic code review with severity grading."""

from weather_agents.core.skill import Skill


def create_skill() -> Skill:
    return Skill(
        name="code_reviewer",
        description="Systematic code review, bug detection, style checking, best-practice validation",
        required_tools=["read_file", "file_search", "code_search"],
        system_prompt="""## 技能：代码审查 (Code Reviewer)
你激活了「代码审查」技能。在此模式下：
1. 按以下维度逐项审查代码：
   🔴 正确性 — 逻辑错误、边界条件、并发问题
   🟡 可维护性 — 命名、结构、注释、复杂度
   🔵 安全性 — 注入、XSS、敏感信息泄漏
   🟢 性能 — 算法效率、资源泄漏、缓存机会
2. 每个问题标注严重等级
3. 给问题附上具体的修复建议和代码示例
4. 最终给出总体评分和优先级排序""",
    )
