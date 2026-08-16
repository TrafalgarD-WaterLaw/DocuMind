# -*- coding: utf-8 -*-
"""CRAG 检索质量评估结论（领域枚举,替代裸字符串 "good"/"poor"）

"""
from __future__ import annotations

from enum import StrEnum


class RetrievalVerdict(StrEnum):
    GOOD = "good"
    POOR = "poor"
