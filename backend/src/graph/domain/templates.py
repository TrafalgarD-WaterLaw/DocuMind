# -*- coding: utf-8 -*-
"""图谱查询模板集 T1-T6（白名单，参数化）——领域知识,禁止自由生成 Cypher

"""
from __future__ import annotations

# ── 模板集（白名单，参数化查询）──
TEMPLATES: dict[str, str] = {
    "T1": "MATCH (a:Artifact {name: $n}) RETURN a.name AS name, a.introduce AS intro",
    "T2": (
        "MATCH (a:Artifact {name: $n})-[r]->(t) "
        "RETURN type(r) AS rel, t.name AS target, labels(t) AS tlabel"
    ),
    "T3": (
        "MATCH (e:Era {name: $e})<-[:BELONGS_TO]-(a:Artifact) "
        "RETURN a.name AS name LIMIT 20"
    ),
    "T4": (
        "MATCH (k:Kiln {name: $k})<-[:BELONGS_TO_KILN]-(a:Artifact) "
        "RETURN a.name AS name LIMIT 20"
    ),
    "T5": (
        "MATCH (s:Site {name: $s})<-[:EXCAVATED_AT]-(a:Artifact) "
        "RETURN a.name AS name LIMIT 20"
    ),
    "T6": (
        "MATCH (a:Artifact)-[:BELONGS_TO]->(e:Era {name: $e}), "
        "(a)-[:EXCAVATED_AT]->(s:Site) "
        "RETURN a.name AS name, s.name AS site LIMIT 20"
    ),
}

# 关系类型 → 中文标签
REL_LABELS = {
    "BELONGS_TO": "属于",
    "EXCAVATED_AT": "出土于",
    "BELONGS_TO_KILN": "属于窑口",
}
