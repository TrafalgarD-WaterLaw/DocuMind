# -*- coding: utf-8 -*-
"""图谱 ODM 模型——neomodel 定义 4 类节点 / 3 类关系

与现有数据契约一致（scripts/import_*_neo4j.py 入库的数据）:
  节点: Artifact(2601) / Site(409) / Era(11) / Kiln(4)
  关系: BELONGS_TO(2575) / EXCAVATED_AT(2803) / BELONGS_TO_KILN(70)

类名即图 label;仅声明已用属性,图谱中额外属性由 neomodel 忽略。
"""
from neomodel import (
    RelationshipFrom,
    RelationshipTo,
    StringProperty,
    StructuredNode,
)


class Artifact(StructuredNode):
    """器物节点——图谱核心实体"""

    name = StringProperty(unique_index=True, required=True)
    introduce = StringProperty()
    image = StringProperty()
    time = StringProperty()
    when = StringProperty()
    where = StringProperty()

    era = RelationshipTo("Era", "BELONGS_TO")
    site = RelationshipTo("Site", "EXCAVATED_AT")
    kiln = RelationshipTo("Kiln", "BELONGS_TO_KILN")


class Site(StructuredNode):
    """遗址节点——器物出土位置"""

    name = StringProperty(unique_index=True, required=True)
    artifacts = RelationshipFrom("Artifact", "EXCAVATED_AT")


class Era(StructuredNode):
    """朝代节点——器物所属时代"""

    name = StringProperty(unique_index=True, required=True)
    artifacts = RelationshipFrom("Artifact", "BELONGS_TO")


class Kiln(StructuredNode):
    """窑口节点——瓷器所属窑口"""

    name = StringProperty(unique_index=True, required=True)
    artifacts = RelationshipFrom("Artifact", "BELONGS_TO_KILN")
