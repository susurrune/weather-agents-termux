"""Skill: Document Analysis — document summarization, key point extraction."""

from weather_agents.core.skill import Skill


def create_skill() -> Skill:
    return Skill(
        name="document_analysis",
        description="Document summarization, key point extraction, comparative analysis",
        required_tools=["read_file", "file_search"],
        system_prompt="""## 技能：文档分析 (Document Analysis)
你激活了「文档分析」技能。在此模式下：
1. 快速提取文档的核心观点和关键信息
2. 识别文档结构（论点、论据、结论）
3. 对技术文档评估完整性和清晰度
4. 多文档时做对比分析，找出异同
5. 输出结构化摘要：概述 → 要点 → 结论 → 建议""",
    )
