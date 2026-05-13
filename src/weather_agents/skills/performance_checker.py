"""Skill: Performance Checker — bottleneck detection and optimization advice."""

from weather_agents.core.skill import Skill


def create_skill() -> Skill:
    return Skill(
        name="performance_checker",
        description="Performance bottleneck detection, algorithm analysis, optimization suggestions",
        required_tools=["read_file", "file_search", "code_search"],
        system_prompt="""## 技能：性能检查 (Performance Checker)
你激活了「性能检查」技能。在此模式下：
1. 分析以下性能维度：
   - 时间复杂度 — 循环嵌套、递归、算法选择
   - 空间复杂度 — 内存分配、大对象、缓存策略
   - I/O 效率 — 数据库查询、网络请求、文件操作
   - 并发性能 — 锁竞争、线程池、异步模式
2. 使用大 O 标记法标注关键路径的复杂度
3. 量化优化收益（预估提速倍数）
4. 给出可落地的优化方案，附代码对比""",
    )
