"""Skill loader — auto-discovers skills from the skills package."""
from __future__ import annotations

from weather_agents.core.skill import Skill, global_skill_registry


def register_all_skills() -> None:
    """Discover and register all built-in skills."""
    _register_skills = _get_all_skills()
    for skill in _register_skills:
        global_skill_registry.register(skill)


def _get_all_skills() -> list[Skill]:
    """Import each skill module and collect Skill instances."""
    from weather_agents.skills.web_research import create_skill as _web_research
    from weather_agents.skills.code_analysis import create_skill as _code_analysis
    from weather_agents.skills.document_analysis import create_skill as _document_analysis
    from weather_agents.skills.code_generator import create_skill as _code_generator
    from weather_agents.skills.content_writer import create_skill as _content_writer
    from weather_agents.skills.data_transformer import create_skill as _data_transformer
    from weather_agents.skills.code_reviewer import create_skill as _code_reviewer
    from weather_agents.skills.security_auditor import create_skill as _security_auditor
    from weather_agents.skills.performance_checker import create_skill as _performance_checker
    from weather_agents.skills.task_planner import create_skill as _task_planner
    from weather_agents.skills.arch_designer import create_skill as _arch_designer
    from weather_agents.skills.workflow_designer import create_skill as _workflow_designer
    from weather_agents.skills.sys_operator import create_skill as _sys_operator
    from weather_agents.skills.ci_cd_manager import create_skill as _ci_cd_manager
    from weather_agents.skills.api_integrator import create_skill as _api_integrator
    return [
        _web_research(), _code_analysis(), _document_analysis(),
        _code_generator(), _content_writer(), _data_transformer(),
        _code_reviewer(), _security_auditor(), _performance_checker(),
        _task_planner(), _arch_designer(), _workflow_designer(),
        _sys_operator(), _ci_cd_manager(), _api_integrator(),
    ]
