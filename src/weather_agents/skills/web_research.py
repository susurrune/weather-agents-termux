"""Skill: Web Research — deep searching and multi-source fact gathering."""

from weather_agents.core.skill import Skill


def create_skill() -> Skill:
    return Skill(
        name="web_research",
        description="Deep web searching, multi-source fact gathering, cross-reference verification",
        required_tools=["web_search", "http_get", "read_file"],
        system_prompt="""## 技能：网络调研 (Web Research)
你激活了「网络调研」技能。在此模式下：
1. 先理解调研目标，确定关键词和搜索策略
2. 从多个来源收集信息，交叉验证事实
3. 标注信息的置信度和来源
4. 给出结构化分析报告，附上引用来源
5. 明确指出信息中的矛盾点和不确定性""",
    )
