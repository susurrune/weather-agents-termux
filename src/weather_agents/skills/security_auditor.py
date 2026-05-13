"""Skill: Security Auditor — security vulnerability scanning and risk assessment."""

from weather_agents.core.skill import Skill


def create_skill() -> Skill:
    return Skill(
        name="security_auditor",
        description="Security vulnerability scanning, OWASP top-10 checks, dependency audit",
        required_tools=["read_file", "file_search", "code_search"],
        system_prompt="""## 技能：安全审计 (Security Auditor)
你激活了「安全审计」技能。在此模式下：
1. 按 OWASP Top 10 维度检查：
   - 注入攻击（SQL、命令、NoSQL）
   - 认证和会话管理缺陷
   - 敏感数据暴露
   - XML 外部实体 (XXE)
   - 访问控制缺陷
   - 安全配置错误
   - XSS 跨站脚本
   - 不安全的反序列化
   - 使用含已知漏洞的组件
   - 日志和监控不足
2. 每个漏洞标注风险等级和 CVSS 评分参考
3. 提供具体的修复方案和代码示例
4. 检查依赖版本中已知的 CVE""",
    )
