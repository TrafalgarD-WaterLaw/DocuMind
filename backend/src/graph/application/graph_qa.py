"""图谱结构化问答服务——意图路由 + 模板化 Cypher 执行（DDD 应用层）

（意图词表/T1-T6 模板）收口 graph/domain,数据访问收口
graph/infrastructure,HTTP 层收口 graph/interfaces。

设计要点：
- 不自由生成 Cypher（防注入/防语法错误），使用预定义模板 T1-T6
- LLM 只做"选模板 + 提取实体"两件事（不设 max_tokens——推理模型思维链会吃 token,截断致空）
- 规则路由先行（朝代/窑口名 + 关系词），LLM 兜底
- 结构化失败自动降级（返回 ok=False，上层走文本检索）

查询执行:neomodel ODM 对象 API（_query_odm,替代手写模板 Cypher），
同步调用经 asyncio.to_thread 包装,不阻塞 async 事件循环。
"""
"""图谱结构化问答服务——意图路由 + 模板化 Cypher 执行

设计要点：
- 不自由生成 Cypher（防注入/防语法错误），使用预定义模板 T1-T6
- LLM 只做"选模板 + 提取实体"两件事（不设 max_tokens——推理模型思维链会吃 token,截断致空）
- 规则路由先行（朝代/窑口名 + 关系词），LLM 兜底
- 结构化失败自动降级（返回 ok=False，上层走文本检索）

查询执行:neomodel ODM 对象 API（_query_odm,替代手写模板 Cypher），
同步调用经 asyncio.to_thread 包装,不阻塞 async 事件循环。
"""
import asyncio
import logging
import re

from interfaces.graph_store import GraphStore
from interfaces.llm import LLMProvider
from prompts import render_system, render_user

logger = logging.getLogger(__name__)

from graph.domain.intent_router import is_structured_query
from graph.domain.templates import REL_LABELS, TEMPLATES

class GraphQueryService:
    """结构化图谱问答"""

    def __init__(self, graph: GraphStore, llm: LLMProvider):
        self.graph = graph
        self.llm = llm

    # ── 意图路由 ─────────────────────────────────────────

    def is_structured(self, query: str) -> bool:
        """规则路由：查询是否适合结构化图谱问答（词表与判定收口 domain/intent_router）"""
        return is_structured_query(query, self.graph)

    # ── 模板选择与执行 ───────────────────────────────────

    async def _select_template(self, query: str) -> dict:
        """LLM 选模板 + 提取实体，返回 {"template": str, "entity": str}

        LLM 输出容错：完整 JSON 优先，正则提取兜底（flash 模型 JSON 易漂移）；
        LLM 失败/返回 none 时用规则兜底（图谱节点名子串匹配 + 实体类型选模板）。
        """
        sel = {"template": "none", "entity": ""}
        try:
            messages = self.llm.build_messages(
                render_system("graph_cypher"),
                render_user("graph_cypher", query=query),
            )
            raw = await self.llm.chat(
                messages, temperature=0.0,
                response_format={"type": "json_object"},  # DeepSeek JSON Output
            )
            sel = self._parse_selection(raw)
        except Exception as e:
            logger.warning(f"模板选择失败: {e}")

        if sel["template"] in TEMPLATES and sel["entity"]:
            return sel

        # 规则兜底：LLM 未选中（none/空实体/失败）→ 图谱名匹配 + 类型选模板
        fb = self._rule_fallback(query)
        if fb["template"] in TEMPLATES and fb["entity"]:
            logger.info(
                f"规则兜底: {fb['template']} 实体={fb['entity']} "
                f"(LLM: {sel['template']}/{sel['entity']})"
            )
            return fb
        return {"template": "none", "entity": ""}

    def _rule_fallback(self, query: str) -> dict:
        """启发式路由——图谱节点名子串匹配（取最长最特异），按类型选模板

        图谱名不带后缀（如"宣德窑"→图谱"宣德"）时，剥离"窑/遗址/墓"后重试。
        """
        candidates = [query]
        for suffix in ("窑", "遗址", "古墓", "墓"):
            if query.endswith(suffix) and len(query) > len(suffix) + 1:
                candidates.append(query[: -len(suffix)])
        # 注意：匹配标签存入独立变量 best_label——若直接用循环变量 label，
        # 循环结束后会被最后一次迭代值（"Artifact"）覆盖，模板映射错乱
        best, best_label = "", ""
        try:
            for cand in candidates:
                for label in ("Era", "Kiln", "Site", "Artifact"):
                    nodes = self.graph.query_nodes(label, limit=300)
                    for n in nodes:
                        name = n.get("name") or ""
                        if (
                            name
                            and len(name) > len(best)
                            and len(name) >= 2
                            and name in cand
                        ):
                            best, best_label = name, label
        except Exception as e:
            logger.warning(f"规则兜底失败: {e}")
            return {"template": "none", "entity": ""}

        if not best:
            return {"template": "none", "entity": ""}
        tmpl = {"Era": "T3", "Kiln": "T4", "Site": "T5", "Artifact": "T2"}[best_label]
        return {"template": tmpl, "entity": best}

    @staticmethod
    def _parse_selection(raw: str) -> dict:
        """容错解析 LLM 输出：JSON 对象 → 正则提取"""
        # 1) 完整 JSON 对象（core.json_utils 提取，失败返回 None）
        from core.json_utils import extract_json

        data = extract_json(raw)
        if data is not None:
            return {
                "template": str(data.get("template", "none")),
                "entity": str(data.get("entity", "")).strip(),
            }
        # 2) 正则提取
        tmpl = re.search(r'["\']?template["\']?\s*[:=]\s*["\']?(T\d|none)["\']?', raw)
        ent = re.search(r'["\']?entity["\']?\s*[:=]\s*["\']([^"\']+)["\']', raw)
        return {
            "template": tmpl.group(1) if tmpl else "none",
            "entity": ent.group(1).strip() if ent else "",
        }

    @staticmethod
    def _entity_candidates(entity: str) -> list[str]:
        """实体名候选：原名 + 去常见后缀（窑/遗址/墓）"""
        candidates = [entity]
        for suffix in ("窑", "遗址", "古墓", "墓"):
            if entity.endswith(suffix) and len(entity) > len(suffix) + 1:
                candidates.append(entity[: -len(suffix)])
        return candidates

    @staticmethod
    def _match_entity(graph: GraphStore, entity: str) -> str | None:
        """实体名匹配：图谱中是否存在精确同名节点

        返回 entity 表示命中；None 表示无此实体或查询失败（降级语义：
        调用方按"图谱无实体"处理）。
        """
        if not entity or len(entity) > 30:
            return None
        try:
            rows = graph.query(
                "MATCH (n {name: $n}) RETURN n.name AS name LIMIT 1", {"n": entity}
            )
            if rows:
                return entity
        except Exception as e:
            logger.warning(f"实体直接匹配查询失败，按不匹配降级: {e}")
        return None

    async def query(self, query_text: str) -> dict:
        """完整结构化查询流程

        Returns:
            {"ok": True, "text": 格式化结果文本, "items": 条目列表} 或
            {"ok": False, "reason": ...}
        """
        sel = await self._select_template(query_text)
        template = sel["template"]
        entity = sel["entity"]

        if template not in TEMPLATES or not entity:
            return {"ok": False, "reason": "模板或实体无法匹配"}

        if template in ("T1", "T2"):
            # 守卫与 ODM 查询均为同步 Neo4j 调用——to_thread 不阻塞事件循环
            if await asyncio.to_thread(
                self._match_entity, self.graph, entity
            ) is None:
                return {"ok": False, "reason": f"图谱中无实体「{entity}」"}
            try:
                rows = await asyncio.to_thread(self._query_odm, template, entity)
            except Exception as e:
                logger.warning(f"图谱查询失败 ({template}): {e}")
                return {"ok": False, "reason": f"查询执行失败: {str(e)[:60]}"}
            if not rows:
                return {"ok": False, "reason": "图谱查询无结果"}

            # 关系覆盖检查：用户问"出土/现藏"但图谱无对应关系时
            # 降级文本检索（散文等文本源可能有该信息）
            if template == "T2":
                want_rel = next(
                    (rel for w, rel in [("出土", "EXCAVATED_AT"), ("现藏", "BELONGS_TO_KILN")]
                     if w in query_text),
                    None,
                )
                if want_rel and want_rel not in {r.get("rel") for r in rows}:
                    return {"ok": False, "reason": f"图谱无 {want_rel} 关系，降级文本检索"}
        elif template in ("T3", "T4", "T5", "T6"):
            rows, entity, error = await self._query_label_aliases(template, entity)
            if error:
                return {"ok": False, "reason": error}
            if not rows:
                return {"ok": False, "reason": "图谱查询无结果"}

        items, text = self._format(template, rows, entity)
        if not text.strip() or text == "（图谱查询无结果）":
            # 结果全为空/占位 → 降级文本检索
            return {"ok": False, "reason": "图谱结果为空"}
        return {"ok": True, "text": text, "items": items}

    async def _query_label_aliases(
        self, template: str, entity: str,
    ) -> tuple[list[dict], str, str]:
        """T3-T6 实体名别名尝试——图谱名可能不带后缀（"宣德窑"→"宣德"）

        Returns: (rows, 命中的实体名, 错误信息)——错误信息非空时 rows 为空,
        调用方按"查询执行失败"降级（保留原 reason 文案）。
        """
        for cand in self._entity_candidates(entity):
            try:
                rows = await asyncio.to_thread(self._query_odm, template, cand)
            except Exception as e:
                logger.warning(f"图谱查询失败 ({template}): {e}")
                return [], cand, f"查询执行失败: {str(e)[:60]}"
            if rows:
                return rows, cand, ""
        return [], entity, ""

    # ── ODM 查询（T1-T6 → neomodel 对象 API，替代手写模板 Cypher）──────

    @staticmethod
    def _query_odm(template: str, entity: str) -> list[dict]:
        """按模板执行 ODM 查询，返回与 _format 契约一致的 rows

        T1{name, intro}  T2{rel, target}  T3-5{name}  T6{site}
        实体不存在返回空列表（调用方降级处理）。
        """
        from graph.infrastructure.graph_models import Artifact, Era, Kiln, Site

        if template == "T1":
            a = Artifact.nodes.get_or_none(name=entity)
            return [{"name": a.name, "intro": a.introduce or ""}] if a else []

        if template == "T2":
            a = Artifact.nodes.get_or_none(name=entity)
            if not a:
                return []
            rows = []
            for rel, mgr in (
                ("BELONGS_TO", a.era),
                ("EXCAVATED_AT", a.site),
                ("BELONGS_TO_KILN", a.kiln),
            ):
                for t in mgr.all():
                    rows.append({"rel": rel, "target": t.name})
            return rows

        if template == "T3":
            e = Era.nodes.get_or_none(name=entity)
            return [{"name": a.name} for a in e.artifacts.all()] if e else []

        if template == "T4":
            k = Kiln.nodes.get_or_none(name=entity)
            return [{"name": a.name} for a in k.artifacts.all()] if k else []

        if template == "T5":
            s = Site.nodes.get_or_none(name=entity)
            return [{"name": a.name} for a in s.artifacts.all()] if s else []

        if template == "T6":
            e = Era.nodes.get_or_none(name=entity)
            rows = []
            if e:
                for a in e.artifacts.all():
                    for s in a.site.all():
                        rows.append({"site": s.name})
            return rows

        return []

    # ── 结果格式化 ───────────────────────────────────────

    @staticmethod
    def _format(template: str, rows: list[dict], entity: str) -> tuple[list[dict], str]:
        """格式化查询结果为证据条目 + 文本（T1-T6 分派到 _format_*）"""
        lines = GraphQueryService._format_lines(template, rows, entity)
        text = "\n".join(lines) if lines else "（图谱查询无结果）"
        source = (
            f"图谱: {entity}遗址分布" if template == "T6"
            else f"图谱: {entity}"
        )
        items = [{
            "source": source,
            "paths": ["graph"],
            "content": "\n".join(lines),
        }]
        return items, text

    @staticmethod
    def _format_lines(template: str, rows: list[dict], entity: str) -> list[str]:
        """按模板生成叙述行（T1-T6 → _format_*）"""
        if template == "T1":
            return GraphQueryService._format_t1(rows, entity)
        if template == "T2":
            return GraphQueryService._format_t2(rows, entity)
        if template in ("T3", "T4", "T5"):
            return GraphQueryService._format_list(rows, entity)
        if template == "T6":
            return GraphQueryService._format_t6(rows, entity)
        return []

    @staticmethod
    def _format_t1(rows: list[dict], entity: str) -> list[str]:
        """T1 器物介绍行"""
        return [
            f"【图谱】{r.get('name', entity)}：{(r.get('intro') or '')[:200]}"
            for r in rows
        ]

    @staticmethod
    def _format_t2(rows: list[dict], entity: str) -> list[str]:
        """T2 关系三元组行（空/占位 target 不输出）"""
        lines: list[str] = []
        for r in rows:
            target = r.get("target") or ""
            if not target or target == "-":
                continue  # 空/占位节点名（数据源 "-"）不输出
            rel = REL_LABELS.get(r.get("rel", ""), r.get("rel", "关联"))
            lines.append(f"【图谱】{entity} —[{rel}]→ {target}")
        return lines

    @staticmethod
    def _format_list(rows: list[dict], entity: str) -> list[str]:
        """T3-T5 相关器物列表行"""
        return [
            f"【图谱】{entity}相关器物：{r.get('name', '')}"
            for r in rows if r.get("name")
        ]

    @staticmethod
    def _format_t6(rows: list[dict], entity: str) -> list[str]:
        """T6 遗址分布行（site 去重）"""
        seen: set[str] = set()
        lines: list[str] = []
        for r in rows:
            site = r.get("site", "")
            if site and site not in seen:
                seen.add(site)
                lines.append(f"【图谱】遗址「{site}」出土商代器物")
        return lines
