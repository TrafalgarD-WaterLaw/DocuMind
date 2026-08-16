# -*- coding: utf-8 -*-
"""深度研究领域类型——专家角色/研究模式/任务与计划（纯领域,无 IO）

"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentRole(StrEnum):
    COORDINATOR = "coordinator"
    IDENTIFIER = "identifier"
    HISTORIAN = "historian"
    CRAFTSMAN = "craftsman"
    RELATOR = "relator"
    SYNTHESIZER = "synthesizer"


class ResearchMode(StrEnum):
    QUICK = "quick"
    DEEP = "deep"


@dataclass
class AgentTask:
    agent: AgentRole
    query: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchPlan:
    mode: ResearchMode
    summary: str
    tasks: list[AgentTask] = field(default_factory=list)
