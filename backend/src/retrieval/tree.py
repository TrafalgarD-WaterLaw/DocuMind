"""树状层级剪枝检索——粗筛定位 → 层级过滤 → 细搜

数据天然呈三层树状结构：
  窑口 (kiln) → 器物 (artifact) → 鉴定维度 (section)

传统扁平检索会把"语义相近但不相关"的段落混进来（如问釉层却召回窑口历史）。
树状剪枝先粗搜定位"相关枝干"，再只在保留的枝干内细搜，降噪提准。
"""
from __future__ import annotations

import logging
from typing import Any

from core.config import settings
from interfaces.vector_store import VectorStore

logger = logging.getLogger(__name__)

# 树状层级：与导入数据的 metadata 字段对应
LEVELS = ["kiln", "artifact"]


class TreeRetriever:
    """层级剪枝检索器（基于 VectorStore 的 where 过滤能力）"""

    def __init__(
        self,
        store: VectorStore,
        *,
        level1_k: int | None = None,      # 粗筛返回数
        level2_k: int | None = None,      # 每层细搜返回数
        max_branches: int | None = None,  # 每层最多保留的枝干数
    ):
        self.store = store
        self.level1_k = level1_k or settings.tree_level1_k
        self.level2_k = level2_k or settings.tree_level2_k
        self.max_branches = max_branches or settings.tree_max_branches

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """剪枝检索 + 扁平纠错（双通道合并）

        1. 全库粗搜，从结果中统计命中的 kiln / artifact
        2. 按层级逐级收窄 where 过滤条件，分支内细搜
        3. 与全库扁平检索结果合并去重——粗筛锁错分支时扁平结果兜底，
           防"粗筛错误传播"（评估：剪枝单独命中 18/22，扁平 20/22，树赢 0 条；
           跨器物查询如"宣德青花釉层特点"的答案分布在多个器物，收窄会丢）

        where 参数可与外部过滤条件叠加。
        """
        # ── 层级 1：全库粗搜，收集枝干集合 ──
        coarse = self.store.retrieve(query, top_k=self.level1_k, where=where)
        if not coarse:
            return []
        branch_filters, pruned = self._collect_branch_filters(coarse, where)

        # ── 层级 2：在收窄集合内细搜（有分支信号时）──
        results: list[dict[str, Any]] = []
        if pruned:
            results = self._refine_search(query, branch_filters)

        # ── 纠错回退：剪枝细搜不足时（分支锁错/数据稀疏），
        # 扁平检索补齐（去重）。剪枝命中足够时保持剪枝排序——
        # 剪枝细搜的排序优势（如器物级精确匹配排第 1）不应被扁平顺序覆盖。
        need = min(top_k, self.level2_k)
        if len(results) < need:
            self._flat_fallback(results, query, top_k, where, need)
        return results[:top_k]

    def _collect_branch_filters(
        self, coarse: list[dict[str, Any]], where: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """逐层收窄：从粗搜结果统计 kiln/artifact 枝干 → 分支过滤条件

        Chroma where 顶层只允许单个条件，多层级条件需用 $and 组合
        （见 _branch_where）。
        """
        branch_filters: list[dict[str, Any]] = []
        if where:
            branch_filters.append(where)
        pruned = False
        for level in LEVELS:
            branches = [
                r.get("metadata", {}).get(level)
                for r in coarse
                if r.get("metadata", {}).get(level)
            ]
            if not branches:
                break
            top_branches = list(dict.fromkeys(branches))[: self.max_branches]
            branch_filters.append({level: {"$in": top_branches}})
            pruned = True
            logger.info(f"树状剪枝 [{level}]: 保留 {top_branches}")
        return branch_filters, pruned

    def _refine_search(
        self, query: str, branch_filters: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """层级 2 细搜 + 剪枝路径标注（可解释性）

        细搜用 level2_k（10），
        收窄集合内多取几条给融合更多选择。
        """
        cond = self._branch_where(branch_filters)
        results = self.store.retrieve(query, top_k=self.level2_k, where=cond)
        for r in results:
            meta = r.get("metadata", {})
            r["pruned_path"] = {
                level: meta.get(level) for level in LEVELS if meta.get(level)
            }
        return results

    def _flat_fallback(
        self, results: list[dict[str, Any]], query: str, top_k: int,
        where: dict[str, Any] | None, need: int,
    ) -> None:
        """纠错回退：扁平检索补齐（去重）至 need 条"""
        flat = self.store.retrieve(query, top_k=top_k, where=where)
        seen = {d.get("id") for d in results}
        for d in flat:
            if d.get("id") not in seen:
                results.append(d)
                seen.add(d.get("id"))
            if len(results) >= need:
                break

    @staticmethod
    def _branch_where(filters: list[dict[str, Any]]) -> dict[str, Any] | None:
        """组合多层过滤条件为 Chroma 合法 where

        Chroma where 顶层只允许一个条件；多条件用 $and 组合。
        """
        if not filters:
            return None
        if len(filters) == 1:
            return filters[0]
        return {"$and": filters}
