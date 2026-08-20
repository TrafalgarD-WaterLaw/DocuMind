"""混合检索器——五路文本召回 + CLIP 文找图路 + RRF 融合 + 图谱锚定

六路召回（其中图谱锚定为增强路,clip 为视觉相似路）：
  路1 semantic: 原始 chunks 向量语义检索（树剪枝只搜文本块 + 图片块直检补 3 条）
  路2 question: 假设性问题索引 Q-to-Q 匹配 → 映射回原始 chunk
  路3 bm25:     关键词精确匹配 (jieba + BM25)
  路4 graph:    查询实体 → Neo4j 关联实体 → 扩展查询词 → 补充语义检索
  路5 entity:   source 名精确匹配（文本实体锚定,可开关）
  路6 clip:     CLIP 文找图视觉命中 → 反查对应文本/图注块（低权重,失败降级）

融合方式：RRF (Reciprocal Rank Fusion)
  score(doc) = Σ 1/(k + rank_i)   (k=60)
每路结果带 paths 标注，可解释召回来源。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from core.config import settings
from core.tracing import RetrievalTrace
from interfaces.graph_store import GraphStore
from interfaces.llm import LLMProvider
from interfaces.vector_store import VectorStore
from prompts import render_system, render_user
from core.llm_retry import llm_call_with_retry
from retrieval.bm25 import BM25Index
from retrieval.tree import TreeRetriever

logger = logging.getLogger(__name__)

# 朝代/窑口宽泛实体（文本实体锚定跳过，由图谱锚定负责）
_BROAD_ENTITIES = {
    "商代", "西周", "春秋", "战国", "秦代", "汉代", "唐代", "宋代",
    "元代", "明代", "清代", "新石器时代", "旧石器时代", "仰韶", "龙山",
}


def _entity_match_rank(d: dict, entity: str) -> tuple:
    """实体匹配质量排序：entities 数组精确命中 > source 名精确等于 > 开头 > 包含

    metadata.entities 数组命中（上传文档实体抽取注入）是强信号——
    上传文档 source 为时间戳前缀，靠 source 名匹配排不进前列。
    """
    src = d.get("metadata", {}).get("source", "")
    entities = d.get("metadata", {}).get("entities") or []
    if isinstance(entities, list) and entity in entities:
        return (-1, src)
    if src == entity:
        return (0, src)
    if src.startswith(entity):
        return (1, src)
    return (2, src)


def _rrf_fuse(
    ranked_paths: list[tuple[str, list[dict[str, Any]]]],
    k: int = 60,
    weights: dict[str, float] | None = None,
) -> dict[str, dict]:
    """RRF 融合多路结果，按文档 id 聚合

    Args:
        ranked_paths: [(路径名, 该路文档列表)]
        k: RRF 平滑参数（settings.rrf_k）
        weights: 路径权重 {路径名: 权重}（graph 路是"扩展词再检索"，
            与 semantic 内容重叠，默认 0.5 降权避免重复计权）

    Returns:
        {doc_id: {"score": float, "paths": set[str], "ranks": {path: rank}}}
    """
    fused: dict[str, dict] = {}
    for path_name, docs in ranked_paths:
        w = (weights or {}).get(path_name, 1.0)
        for rank, doc in enumerate(docs):
            doc_id = doc.get("id") or doc.get("metadata", {}).get("source")
            if not doc_id:
                continue
            entry = fused.setdefault(
                doc_id, {"score": 0.0, "paths": set(), "ranks": {}}
            )
            entry["score"] += w / (k + rank)
            entry["paths"].add(path_name)
            entry["ranks"][path_name] = rank
    return fused


class HybridRetriever:
    """五路文本召回 + CLIP 文找图路 + RRF 融合检索器"""

    def __init__(
        self,
        doc_store: VectorStore,
        question_store: VectorStore,
        bm25: BM25Index,
        graph: GraphStore | None = None,
        llm: LLMProvider | None = None,
        reranker: Any = None,
        *,
        path_k: int | None = None,
        top_k: int | None = None,
        rrf_k: int | None = None,
    ):
        self.doc_store = doc_store
        self.question_store = question_store
        self.bm25 = bm25
        self.graph = graph
        self.llm = llm
        self.reranker = reranker  # cross-encoder 精排器（None 时退化为纯 RRF）
        self.path_k = path_k or settings.hybrid_path_k
        self.question_path_k = settings.question_path_k
        self.top_k = top_k or settings.hybrid_top_k
        self.rrf_k = rrf_k or settings.rrf_k
        self.rerank_candidates = settings.rerank_candidates  # 精排候选数
        # 启用 rerank 时扩大每路召回（32 候选才有精排空间；否则在
        # 已高相关的 16 条内做微排序，微小噪声会主导结果）
        self.rerank_path_k = settings.rerank_path_k
        self.graph_weight = settings.rrf_graph_weight   # graph 路降权
        self.question_weight = settings.rrf_question_weight  # question 路强信号权重
        self.candidate_pool = settings.candidate_pool  # 无 rerank 时的候选池大小
        # 实体提取缓存（graph 锚定与文本锚定共用一次 LLM 调用）
        self._entity_cache_query = ""
        self._entity_cache = ""
        # 树状剪枝：semantic 路粗筛定位窑口/器物 → 收窄细搜（降噪）
        self.tree = TreeRetriever(doc_store)

    # ── 各路径召回 ────────────────────────────────────────

    def _path_semantic(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """语义路 = 树状层级剪枝检索 + 图片块独立直检（双通道合并）

        树剪枝的 where 过滤按 kiln/artifact 收窄，图片块 metadata 无这些
        字段会被结构性排除（且细搜命中 ≥ top_k 时纠错回退不触发）；图片块
        单独直检（where={"chunk_type": "image"}）绕过剪枝过滤作为补充通道，
        保证图注级图片可被语义路召回（最多补 3 条）。
        注意：补充的图片块直接截断会再次被丢弃（树剪枝已返回满 top_k 时），
        故上限放宽为 cap+补数，多出的图片块交给 RRF 融合去留（image 权重
        降权,见 _build_candidates）。
        """
        cap = top_k or self.path_k
        extra = settings.image_chunk_top_k
        # 树剪枝只搜文本块:图片块(图注)由直检通道单独召回。补 VLM 描述后
        # 图片块语义相似度最高,参与树剪枝会占满粗搜名额→分支信号丢失
        # (图片块无 kiln/artifact)→ 剪枝失效+扁平回退拉回图片块,
        # 文本块被挤出 semantic 路(实测 Recall@8 100%→96%)。
        results = self.tree.retrieve(
            query, top_k=cap, where={"chunk_type": "text"}
        )
        try:
            imgs = self.doc_store.retrieve(
                query, top_k=extra, where={"chunk_type": "image"}
            )
            seen = {d.get("id") for d in results}
            for d in imgs:
                if d.get("id") not in seen:
                    results.append(d)
                    seen.add(d.get("id"))
        except Exception as e:
            logger.warning(f"图片块直检失败: {e}")
        return results[: cap + extra]

    def _path_question(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """Q-to-Q 匹配：在问题索引上检索 → 映射回原始 chunk

        question 索引 1.6 万条，top-8 检索太窄（假设问题间相似度高，
        目标问题常排 9-30 名）——独立用 question_path_k（默认 30）。
        """
        q_results = self.question_store.retrieve(
            query, top_k=top_k or self.question_path_k
        )
        if not q_results:
            return []

        chunk_ids = []
        for r in q_results:
            cid = r.get("metadata", {}).get("source_chunk_id")
            if cid and cid not in chunk_ids:
                chunk_ids.append(cid)

        originals = self.doc_store.get_by_ids(chunk_ids)
        # 保留 Q-to-Q 的排序（映射回原文后仍按问题路排序）
        by_id = {d["id"]: d for d in originals}
        ordered = []
        for cid in chunk_ids:
            doc = by_id.get(cid)
            if doc:
                ordered.append({
                    "id": doc["id"],
                    "content": doc["content"],
                    "source": doc.get("metadata", {}).get("source", ""),
                    "score": 0.0,
                    "metadata": doc["metadata"],
                })
        return ordered

    def _path_bm25(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        return self.bm25.retrieve(query, top_k=top_k or self.path_k)

    async def _path_entity_anchor(self, query: str) -> list[dict[str, Any]]:
        """文本实体锚定：实体名按 source 精确匹配（短条目 embedding 劣势补偿）

        短条目（一行索引，如"【商代】叩鼎"）embedding 信息少，语义检索
        排不过长文档；但器名精确匹配是强信号——SQLite 子串匹配直接取回，
        不依赖 embedding 相似度。
        """
        if not settings.entity_anchor_enabled:
            return []
        entity = await self._extract_entity_cached(query)
        candidates = self._split_entity_candidates(entity, query)
        if not candidates:
            return []

        results: list[dict[str, Any]] = []
        seen_src: set[str] = set()
        for candidate in candidates:
            matches = self._collect_entity_matches(candidate)
            for d in matches:
                src = d.get("metadata", {}).get("source", "")
                if src in seen_src:
                    continue
                seen_src.add(src)
                results.append({
                    "id": d["id"],
                    "content": d["content"],
                    "source": src,
                    "score": 0.0,
                    "metadata": d["metadata"],
                    "paths": ["entity"],
                })
                if len(results) >= 2:  # 最多 2 个 source（配合多样性）
                    break
            if len(results) >= 2:
                break
        if results:
            logger.info(
                f"文本实体锚定: 「{'/'.join(candidates)}」→ "
                f"{[r['source'][:20] for r in results]}"
            )
        return results

    def _split_entity_candidates(self, entity: str, query: str) -> list[str]:
        """实体串 → 候选列表：顿号拆分 + 规则回退 + 宽泛实体过滤"""
        # 多实体拆分（LLM 可能返回"商代、青铜鼎"顿号分隔）
        candidates = [
            c for c in re.split(r"[、,，/\s]+", entity)
            if c and len(c) >= 2 and c not in ("无", "none", "None")
        ]
        if not candidates:
            # LLM 提取失败 → 规则回退：查询与 documents source 名集合最长子串匹配
            # （不依赖图谱——青铜器名不在 Era/Kiln 节点里）
            rule = self._rule_entity_from_sources(query)
            if rule:
                candidates = [rule]
        # 朝代/窑口等宽泛实体不锚定文本（匹配面太广——"商代"会命中
        # "商代原始瓷尊"等无关器物；朝代类由图谱锚定负责）
        return [c for c in candidates if not self._is_broad_entity(c)]

    def _collect_entity_matches(self, entity: str) -> list[dict[str, Any]]:
        """单实体取回匹配块：source 子串 + metadata.entities 数组，按质量排序

        按 source 名与实体名的匹配质量排序（精确 > 前缀 > 包含），
        避免"鼎"这类宽泛实体匹配到大量无关 source。
        """
        try:
            matches = self.doc_store.get_by_source_like(entity)
        except Exception as e:
            logger.warning(f"文本实体锚定失败 ({entity}): {e}")
            matches = []
        # U2 扩展：metadata.entities 数组匹配（上传文档 source 为时间戳
        # 前缀，source 名匹配天然失效——实体抽取注入的 entities 字段
        # 让上传文档也能被实体锚定；Chroma 数组 $contains 过滤）
        try:
            ematches = self.doc_store.get_by_where(
                {"entities": {"$contains": entity}}, limit=4
            )
            existing_ids = {m["id"] for m in matches}
            for d in ematches:
                if d.get("id") not in existing_ids:
                    matches.append(d)
        except Exception as e:
            logger.warning(f"实体数组锚定失败 ({entity}): {e}")
        matches.sort(key=lambda d: _entity_match_rank(d, entity))
        return matches

    async def _path_clip(self, query: str) -> list[dict[str, Any]]:
        """CLIP 文找图路——视觉相似命中 → 反查对应文本/图注块（第 6 路）

        text_search 按图文同空间余弦返回外观相似的图片（source 即器物名）；
        命中的 source 反查 documents 的文本块/图注块作为候选参与 RRF——
        覆盖"外观相似/像什么"类问题（图注里没有"外观相似"的描述，
        纯文本五路基本漏）。失败/CLIP 未加载时返回空（降级,不阻断）。
        """
        top_k = settings.clip_retrieval_top_k
        try:
            from multimodal.clip_retrieval import clip_retriever

            hits = await clip_retriever.text_search(query, top_k=top_k)
        except Exception as e:
            logger.warning(f"CLIP 文找图失败,该路降级为空: {e}")
            return []
        if not hits:
            return []
        order, keys = self._clip_hit_keys(hits)
        try:
            blocks = await asyncio.to_thread(
                self.doc_store.get_by_where,
                {"source": {"$in": keys}},
                limit=top_k * 4,
            )
        except Exception as e:
            logger.warning(f"CLIP 命中反查文本块失败: {e}")
            return []
        results = self._clip_blocks_to_docs(blocks, order)
        if results:
            logger.info(
                f"CLIP 文找图路: 「{query[:30]}」→ "
                f"{[r['source'][:20] for r in results[:3]]}"
            )
        return results

    @staticmethod
    def _clip_hit_keys(hits: list[dict]) -> tuple[dict[str, int], list[str]]:
        """视觉命中 → (source→次序, 双 key 反查列表)

        clip_images 数据集图片 source 不带 #图,documents 文本块也不带、
        图注块带——双 key 反查两变体。
        """
        order: dict[str, int] = {}
        keys: list[str] = []
        for i, h in enumerate(hits):
            src = h.get("source", "")
            if not src:
                continue
            order[src] = i
            keys.append(src)
            if not src.endswith("#图"):
                keys.append(f"{src}#图")
                order[f"{src}#图"] = i
        return order, keys

    @staticmethod
    def _clip_blocks_to_docs(
        blocks: list[dict], order: dict[str, int],
    ) -> list[dict[str, Any]]:
        """反查块按视觉相似度排序 → 统一块结构（每 source 最多 2 块,防垄断）"""
        blocks.sort(key=lambda b: order.get(
            b.get("metadata", {}).get("source", ""), 999,
        ))
        results: list[dict[str, Any]] = []
        src_count: dict[str, int] = {}
        for b in blocks:
            src = b.get("metadata", {}).get("source", "")
            if src_count.get(src, 0) >= 2:
                continue
            src_count[src] = src_count.get(src, 0) + 1
            results.append({
                "id": b["id"],
                "content": b["content"],
                "source": src,
                "score": 0.0,  # RRF 只看排名,CLIP 余弦仅用于路内排序
                "metadata": b["metadata"],
                "paths": ["clip"],
            })
        return results

    def _is_broad_entity(self, entity: str) -> bool:
        """朝代/窑口宽泛实体：图谱 Era/Kiln 节点命中即跳过（匹配面太广）"""
        if entity in _BROAD_ENTITIES:
            return True
        if self.graph is not None:
            try:
                for label in ("Era", "Kiln"):
                    nodes = self.graph.query_nodes(label, limit=100)
                    if any(n.get("name") == entity for n in nodes):
                        return True
            except Exception as e:
                logger.warning(f"宽泛实体判定图谱查询失败，按非宽泛处理: {e}")
        return False

    def _rule_entity_from_sources(self, query: str) -> str:
        """规则实体回退：查询文本与 documents source 名集合最长子串匹配

        覆盖图谱回退的盲区（图谱只有 Era/Kiln/Artifact 的图谱名，
        青铜器 PDF 器物名不在图谱中）。剥离来源前缀后匹配最长的器物名。
        """
        try:
            sources = self.doc_store.list_sources()
        except Exception:
            return ""
        best = ""
        for s in sources:
            name = re.sub(r"^(青铜|河南博物院|窑口|宣德|洪武|永乐|元代)-", "", s).strip()
            if len(name) > len(best) and len(name) >= 2 and name in query:
                best = name
        return best

    async def _path_graph(self, query: str) -> list[dict[str, Any]]:
        """图谱锚定：提取实体 → 查关联 → 扩展查询词补一轮语义检索

        宽泛实体（朝代）枚举分支：纯文本检索对"春秋有什么青铜器"这类
        问题召回不足（图谱器物名不在 top-8）——图谱 T3 能精确枚举朝代下
        全部器物，直接把枚举列表作为图谱证据块进 sources（不再只靠
        扩展词语义检索，扩展词路径对 563 个关联的朝代枚举失效）。
        """
        if self.graph is None:
            return []

        entity = await self._extract_entity_cached(query)
        if not entity:
            return []

        # LLM 可能返回多个候选（如"商代 青铜器"），拆分后逐个尝试
        candidates = [
            c for c in re.split(r"[、,，/\s]+", entity)
            if c and c not in ("无", "none", "None")
        ]

        # ── 宽泛实体（朝代）枚举分支 ──
        # "X朝有什么青铜器/文物"：图谱 T3 枚举朝代器物，直接作为证据块。
        # 判定：实体命中 Era 节点 + 查询含列举意图词。图谱枚举是确定性
        # 结果（比语义检索更准），且解决"图谱器物进不了 top-8"的召回盲区。
        list_hint = any(w in query for w in ("什么", "有哪些", "列举", "几种", "几个", "哪些"))
        broad = [c for c in candidates if self._is_broad_entity(c)]
        if broad and list_hint:
            era_name = broad[0]
            try:
                rows = await asyncio.to_thread(
                    self.graph.query,
                    "MATCH (e:Era {name: $e})<-[:BELONGS_TO]-(a:Artifact) "
                    "RETURN a.name AS name LIMIT 30",
                    {"e": era_name},
                )
            except Exception as e:
                logger.warning(f"图谱朝代枚举失败 ({era_name}): {e}")
                rows = []
            names = [r["name"] for r in rows if r.get("name")]
            if names:
                lines = [f"【图谱】{era_name}相关器物：{n}" for n in names]
                return [{
                    "id": f"graph-enum-{era_name}",
                    "content": "\n".join(lines),
                    "source": f"图谱: {era_name}",
                    "score": 0.0,
                    "metadata": {"source": f"图谱: {era_name}"},
                    "paths": ["graph"],
                    "graph_anchor": {
                        "entity": era_name,
                        "related": names[:10],
                        "links": [{"source": era_name, "name": "BELONGS_TO",
                                   "target": n} for n in names[:10]],
                    },
                }]

        entity, links = self._resolve_graph_links(candidates)
        if not links:
            return []

        # 关联实体在 links 中（search_path 的 nodes 仅含查询实体本身）
        related = [link["target"] for link in links if link.get("target") != entity][:5]
        if not related:
            return []
        return self._build_graph_docs(entity, related, links)

    def _resolve_graph_links(
        self, candidates: list[str],
    ) -> tuple[str, list[dict]]:
        """逐个候选查图谱关联,取首个有关系的候选"""
        for cand in candidates:
            try:
                _, links = self.graph.search_path(cand)
            except Exception as e:
                logger.warning(f"图谱锚定失败 ({cand}): {e}")
                continue
            if links:
                return cand, links
        return "", []

    def _build_graph_docs(
        self, entity: str, related: list[str], links: list[dict],
    ) -> list[dict[str, Any]]:
        """扩展检索词补一轮语义检索 + 子图三元组证据块"""
        # 扩展检索词；links 三元组透传前端渲染图谱子图
        expanded = f"{entity} {' '.join(related)}"
        anchor = {"entity": entity, "related": related, "links": links[:10]}
        results = self.doc_store.retrieve(expanded, top_k=self.path_k)
        for r in results:
            r["graph_anchor"] = anchor

        # 子图增强：关系三元组文本化，作为一条图谱证据进 sources
        # （LLM 可见 BELONGS_TO/EXCAVATED_AT 等关系语义，支持路径推理）
        rel_lines = [
            f"【图谱】{link.get('source', entity)} —[{link.get('name', '关联')}]→ {link.get('target', '?')}"
            for link in links[:10]
        ]
        results.append({
            "id": f"graph-subgraph-{entity}",
            "content": "\n".join(rel_lines),
            "source": f"图谱: {entity} 关系",
            "score": 0.0,
            "metadata": {"source": f"图谱: {entity} 关系"},
            "paths": ["graph"],
            "graph_anchor": anchor,
        })
        return results

    async def _extract_entity_cached(self, query: str) -> str:
        """实体提取 + 缓存（graph 锚定与文本锚定共用，避免重复 LLM 调用）"""
        if self._entity_cache_query == query:
            return self._entity_cache
        self._entity_cache_query = query
        self._entity_cache = await self._extract_entity(query)
        return self._entity_cache

    async def _extract_entity(self, query: str) -> str:
        """从查询中提取文物实体名

        优先 LLM 提取（不设 max_tokens——deepseek-v4-flash 思维链吃 token,截断致空）
        开销，过小会让最终内容为空）；LLM 返回空时回退到规则匹配
        （从图谱动态拉取 Era/朝代名做子串匹配，零成本且稳定）。
        """
        # 1) LLM 提取
        if self.llm is not None:
            try:
                messages = self.llm.build_messages(
                    render_system("entity_extraction"),
                    render_user("entity_extraction", query=query),
                )
                raw = await llm_call_with_retry(
                    messages, self.llm, temperature=0.0,
                    response_format={"type": "json_object"},  # DeepSeek JSON Output
                )
                # JSON 结构解析（空 content/解析失败时走规则回退）
                from core.json_utils import extract_json

                data = extract_json(raw)
                name = str((data or {}).get("entity", "")).strip()
                if name and name not in ("无", "none", "None"):
                    return name
            except Exception as e:
                logger.warning(f"LLM 实体提取失败: {e}")

        # 2) 规则回退：图谱朝代/窑口名子串匹配（零成本且稳定）
        if self.graph is not None:
            try:
                for label in ("Era", "Kiln"):
                    nodes = self.graph.query_nodes(label, limit=100)
                    for n in nodes:
                        if n.get("name") and n["name"] in query:
                            return n["name"]
            except Exception as e:
                logger.warning(f"规则实体回退失败: {e}")
        return ""

    # ── 主入口 ────────────────────────────────────────────

    async def _try_graph_answer(
        self, query: str, trace: RetrievalTrace | None,
    ) -> list[dict[str, Any]] | None:
        """意图路由近路:图谱类问题 → 图谱问答直答（命中返回证据,未命中 None）

        图谱是关系类问题的权威来源（模板化 Cypher T1-T6，不自由生成）；
        失败（实体不在图谱/无该关系）→ None，调用方降级走五路文本检索。
        """
        import time as _time

        from graph.application.graph_qa import GraphQueryService

        gq = GraphQueryService(self.graph, self.llm)
        if not gq.is_structured(query):
            return None
        t0 = _time.perf_counter()
        result = await gq.query(query)
        if not result["ok"]:
            return None
        gdocs = self._graph_to_docs(result)
        if trace is not None:
            trace.record_path("graph", len(gdocs), (_time.perf_counter() - t0) * 1000)
        return gdocs

    async def _recall_paths(
        self, query: str, use_graph: bool, top_k: int,
        trace: RetrievalTrace | None,
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        """五路召回——graph/entity 的 LLM 提取与三路同步检索重叠

        上传/删除文档后 BM25 置脏先惰性重建；启用 rerank 时扩大每路召回
        （候选 32 条才有精排空间）。
        """
        import time as _time

        try:
            self.bm25.rebuild_if_dirty(self.doc_store)
        except Exception as e:
            logger.warning(f"BM25 惰性重建失败: {e}")

        path_k = self.rerank_path_k if self.reranker is not None else self.path_k
        # graph 路的首个耗时点是 LLM 实体提取（秒级）——提前启动与三路
        # 同步检索重叠。行为等价：graph 先跑 → entity 复用其实体缓存
        # （_extract_entity_cached，单次 LLM 契约保持）。
        graph_task = (
            asyncio.create_task(self._path_graph(query)) if use_graph else None
        )
        ranked_paths = await self._recall_sync_paths(query, path_k, trace)
        if graph_task is not None:
            t0 = _time.perf_counter()
            gdocs = await graph_task
            ranked_paths.append(("graph", gdocs))
            if trace is not None:
                trace.record_path("graph", len(gdocs), (_time.perf_counter() - t0) * 1000)
        if settings.entity_anchor_enabled:
            t0 = _time.perf_counter()
            edocs = await self._path_entity_anchor(query)
            ranked_paths.append(("entity", edocs))
            if trace is not None:
                trace.record_path("entity", len(edocs), (_time.perf_counter() - t0) * 1000)
        # CLIP 文找图路(第 6 路):视觉相似命中参与 RRF——低权重
        # (clip_path_weight),覆盖"外观相似"类问题;CLIP 未加载/失败降级为空
        t0 = _time.perf_counter()
        cdocs = await self._path_clip(query)
        ranked_paths.append(("clip", cdocs))
        if trace is not None:
            trace.record_path("clip", len(cdocs), (_time.perf_counter() - t0) * 1000)
        return ranked_paths

    async def _recall_sync_paths(
        self, query: str, path_k: int, trace: RetrievalTrace | None,
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        """semantic/question/bm25 三路同步检索（to_thread,逐路记 trace）"""
        import time as _time

        ranked_paths: list[tuple[str, list[dict[str, Any]]]] = []
        for path_name, call in (
            ("semantic", lambda: self._path_semantic(query, top_k=path_k)),
# question 路独立用 question_path_k（30）——问题索引 1.6 万条，
            # top-8 太窄（目标问题常排 9-30 名）；传 None 走 _path_question
            # 内部 `top_k or self.question_path_k`，不再被 path_k(8) 覆盖
            ("question", lambda: self._path_question(query, top_k=None)),
            ("bm25", lambda: self._path_bm25(query, top_k=path_k)),
        ):
            t0 = _time.perf_counter()
            # to_thread：同步 Chroma 查询不再阻塞事件循环；每次 await 也让
            # graph_task 的 LLM 提取（秒级）与三路检索真正重叠
            docs = await asyncio.to_thread(call)
            ranked_paths.append((path_name, docs))
            if trace is not None:
                trace.record_path(path_name, len(docs), (_time.perf_counter() - t0) * 1000)
        return ranked_paths

    def _build_candidates(
        self,
        ranked_paths: list[tuple[str, list[dict[str, Any]]]],
        max_per_source: int,
    ) -> list[dict[str, Any]]:
        """RRF 融合 → 图片块降权 → 来源多样性 → 候选集（候选池条数）"""
        fused = _rrf_fuse(
            ranked_paths,
            k=self.rrf_k,
            weights={
                "graph": self.graph_weight,
                "question": self.question_weight,
                "entity": 1.0,  # 器名精确匹配是强信号，全权重
                "clip": settings.clip_path_weight,  # 视觉相似低权重
            },
        )
        self._downweight_image_entries(fused, ranked_paths)
        # 候选池大小：rerank 时用精排候选数（32），否则用 candidate_pool（64）。
        # 32 太小会截断单票 chunk（无实体名查询只靠 question 路 1 票）。
        candidate_limit = self.rerank_candidates if self.reranker else self.candidate_pool
        return self._apply_source_diversity(
            fused, ranked_paths, candidate_limit, max_per_source,
        )

    def _downweight_image_entries(
        self, fused: dict[str, dict],
        ranked_paths: list[tuple[str, list[dict[str, Any]]]],
    ) -> None:
        """图片块(图注)降权:补 VLM 描述后图注块语义相似度升高,与文本块
        同权会挤占文本证据(实测河南图注块挤掉 bronze 文本块,Recall 回归);
        权重 0.3——文本证据弱时仍可浮上,不改变五路路径结构与前端渲染。
        0.3 是文本证据与图片块浮出的平衡点(1.0 时 MRR 0.693→0.688),
        多票自然命中的图注块会挤占文本证据。
        """
        for doc_id, entry in list(fused.items()):
            doc = self._pick_doc(doc_id, ranked_paths)
            if (doc.get("metadata", {}) or {}).get("chunk_type") == "image":
                entry["score"] *= settings.image_evidence_weight

    def _apply_source_diversity(
        self, fused: dict[str, dict],
        ranked_paths: list[tuple[str, list[dict[str, Any]]]],
        candidate_limit: int, max_per_source: int,
    ) -> list[dict[str, Any]]:
        """按融合分排序 + 来源多样性截断 → 候选集"""
        candidates: list[dict[str, Any]] = []
        for doc_id, entry in sorted(
            fused.items(), key=lambda kv: kv[1]["score"], reverse=True
        ):
            doc = self._pick_doc(doc_id, ranked_paths)
            # 来源多样性：同一 source 最多保留 max_per_source 条，
            # 避免窑口简介等大块内容垄断结果（如 "窑口-宣德" × N 挤掉器物级）
            src = doc.get("source", "")
            if src and sum(
                1 for r in candidates if r.get("source") == src
            ) >= max_per_source:
                continue
            candidates.append(self._build_result(doc_id, entry, doc))
            if len(candidates) >= candidate_limit:
                break
        return candidates

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        use_graph: bool = True,
        max_per_source: int = 2,
        trace: RetrievalTrace | None = None,
        graph_intent: bool = False,
    ) -> list[dict[str, Any]]:
        """五路召回 + RRF 融合 + 可选 cross-encoder 精排

        主流程四步：图谱近路（graph_intent）→ 五路召回 → 融合候选
        → 精排截断（reranker 为 None 时退化为纯 RRF 截断）。

        graph_intent 仅 orchestrator 开启（问答近路）；eval 基线/其他调用方
        默认关闭——保证检索契约:graph 路 = 图谱锚定 → 文本检索不受影响
        （图谱问答返回图谱叙述，eval 期望文本块，短路会破坏召回评测，
          实测 Recall 100%→89%）。

        Returns:
            [{id, content, source, score, metadata, paths: [...], ...}]
        """
        top_k = top_k or self.top_k

        # 1. 意图路由近路:图谱类问题（问朝代/出土/窑口关系）→ 图谱直答
        if graph_intent and use_graph and self.graph is not None:
            gdocs = await self._try_graph_answer(query, trace)
            if gdocs:
                return gdocs

        # 2. 五路召回（BM25 惰性重建 + graph/entity LLM 提取与三路重叠）
        ranked_paths = await self._recall_paths(query, use_graph, top_k, trace)

        # 3. 融合 + 图片降权 + 来源多样性 → 候选集
        candidates = self._build_candidates(ranked_paths, max_per_source)

        # 4. 精排：cross-encoder 交互式打分（替代"排名倒数"融合分）
        if self.reranker is not None:
            candidates = self._apply_rerank(query, candidates)

        results = candidates[:top_k]
        # 图谱枚举块保底（朝代→器物等确定性枚举结果）：RRF 单票分数
        # （graph 权重 0.5/61）会被语义路挤掉，但它是图谱精确枚举——
        # 语义检索无法替代，保底附加到结果集（去重后不超过 top_k）。
        # 不改变其他查询的检索契约（无枚举块时行为不变）。
        enum_blocks = [
            d for _, docs in ranked_paths for d in docs
            if d.get("id", "").startswith("graph-enum-")
        ]
        if enum_blocks:
            existing_ids = {r["id"] for r in results}
            for eb in enum_blocks:
                if eb["id"] in existing_ids:
                    continue
                # 枚举块保底插入顶部；结果已满 top_k 时挤掉最后一条
                # （普通文本块——枚举是确定性精确结果，优先级更高）
                if len(results) >= top_k:
                    results.pop()
                results.insert(0, eb)
                existing_ids.add(eb["id"])
        self._log_completion(results, query, trace)
        return results

    def _apply_rerank(
        self, query: str, candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """cross-encoder 精排,score 替换为 rerank_score（保留 4 位）"""
        candidates = self.reranker.rerank(query, candidates)
        for r in candidates:
            r["score"] = round(r.get("rerank_score", 0.0), 4)
        return candidates

    def _log_completion(
        self, results: list[dict[str, Any]], query: str,
        trace: RetrievalTrace | None,
    ) -> None:
        """完成日志 + trace 路径统计（检索诊断面板数据）"""
        if results:
            logger.info(
                f"混合检索完成: {len(results)} 条，"
                f"路分布={self._path_stats(results)}"
                + ("，rerank 已启用" if self.reranker is not None else "")
            )
        if trace is not None:
            trace.set_path_stats(self._path_stats(results))

    @staticmethod
    def _graph_to_docs(result: dict) -> list[dict[str, Any]]:
        """图谱问答结果 → 统一证据块（与文本检索结果同构——证据链/CRAG/生成共用）

        主块 = 模板 Cypher 生成的叙述文本（text）；其余 items 作为并列证据块。
        source 标注"图谱"、paths 标 graph（前端流水线/证据链按路渲染）。
        """
        docs: list[dict[str, Any]] = [{
            "id": "graph-0",
            "content": result.get("text", ""),
            "source": "图谱",
            "paths": ["graph"],
            "metadata": {},
        }]
        for i, it in enumerate(result.get("items", [])):
            docs.append({
                "id": f"graph-{i + 1}",
                "content": it.get("content", ""),
                "source": it.get("source", "图谱"),
                "paths": ["graph"],
                "metadata": {},
            })
        return docs

    @staticmethod
    def _pick_doc(
        doc_id: str, ranked_paths: list[tuple[str, list[dict[str, Any]]]]
    ) -> dict[str, Any]:
        """从任意一路取文档详情（优先带 graph_anchor 的图谱路文档，
        保证锚定信息可透传到引用展示）"""
        first: dict[str, Any] | None = None
        for path_name, docs in ranked_paths:
            d = next((d for d in docs if d.get("id") == doc_id), None)
            if d:
                if d.get("graph_anchor"):
                    return d
                if first is None:
                    first = d
        # fused 的 doc_id 必来自某一路——理论上不会走到这里,兜底返回
        # 空块(而非依赖隐式不变量导致 UnboundLocalError)
        if first is not None:
            return first
        logger.warning(f"融合结果 {doc_id} 未在任何路中找到详情,返回空块")
        return {"id": doc_id, "content": "", "source": "", "metadata": {}}

    @staticmethod
    def _build_result(
        doc_id: str, entry: dict, doc: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "id": doc_id,
            "content": doc.get("content", ""),
            "source": doc.get("source", ""),
            "score": round(entry["score"], 4),
            "metadata": doc.get("metadata", {}),
            "paths": sorted(entry["paths"]),
            "rrf_ranks": entry["ranks"],
            "graph_anchor": doc.get("graph_anchor"),
        }

    @staticmethod
    def _path_stats(results: list[dict]) -> dict[str, int]:
        """统计各路径命中次数（用于日志/展示）"""
        stats: dict[str, int] = {}
        for r in results:
            for p in r.get("paths", []):
                stats[p] = stats.get(p, 0) + 1
        return stats
