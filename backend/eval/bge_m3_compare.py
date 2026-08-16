# -*- coding: utf-8 -*-
"""B2 bge-m3 升级评估——稠密向量 semantic 路对比（bge-small vs bge-m3）

独立 Chroma collection（不动生产库）重 embedding 全库，对核心集逐查询
对比 semantic 单路 Recall@8 / MRR（期望来源命中）——数据决定是否切换。

用法: python eval/bge_m3_compare.py
输出: 控制台对比表 + eval/reports/bge_m3_compare_*.json
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import chromadb  # noqa: E402

from core.di import container  # noqa: E402

EVAL_DIR = Path(__file__).parent
REPORTS_DIR = EVAL_DIR / "reports"
M3_PATH = "D:/cache/modelscope/models/BAAI--bge-m3"
LIST_WORDS = ("哪些", "有什么", "列举", "几种", "几个")


def load_cases() -> list[dict]:
    return json.loads((EVAL_DIR / "dataset.json").read_text(encoding="utf-8"))["cases"]


class _M3EmbedFn:
    """bge-m3 稠密向量适配（ST 5.x 返回多向量对象，取 dense）"""

    def __init__(self, model):
        self._model = model

    def name(self) -> str:
        """Chroma ef 冲突校验需要 name"""
        return "bge-m3"

    def __call__(self, input: list[str]) -> list[list[float]]:
        """Chroma EmbeddingFunction 接口（新版签名 __call__(self, input)）"""
        out = self._model.encode(input, batch_size=32)
        # ST 5.x BGE-M3: 返回 Embedding 对象（dense/sparse/colbert）或 ndarray
        if hasattr(out, "dense"):
            return [v.tolist() for v in out.dense]
        if isinstance(out, list) and hasattr(out[0], "dense"):
            return [v.dense.tolist() for v in out]
        return [v.tolist() for v in out]


def _build_m3_collection(all_docs: list[dict]) -> tuple[chromadb.Collection, dict]:
    """bge-m3 重 embedding 到独立 collection（documents_m3_eval，不动生产库）

    Returns:
        (collection, sparse_map, model)——sparse_map: {doc_id: {token_id: weight}}，
        B3 稀疏向量替换 BM25 验证复用（一次编码同时产出 dense + sparse）。
    """
    from sentence_transformers import SentenceTransformer

    # Chroma 的 embedding_function 不持久化——每次进程打开都要传入
    model = SentenceTransformer(M3_PATH, trust_remote_code=True)
    # 关键：max_seq_length 8192 → 512——子块（≤250 字）无损，长父块
    # （1500 字拼接）截到前半（标题+开头语义），4GB 显存下编码提速 10 倍+
    # （实测 batch 8 × 1500 字长尾会让 GPU 单批 10-30s，全库 2 小时）
    model.max_seq_length = 512
    model.half()  # fp16：1650 Ti 无 fp32 加速（长文本批 3-6s/批 是硬件天花板），
    # 半精度 2-4 倍加速 + 显存减半（2GB），batch 可加大到 32
    client = chromadb.PersistentClient(
        path=str(Path(__file__).parent.parent / "src" / "data" / "chroma")
    )
    col = client.get_or_create_collection(
        "documents_m3_eval", embedding_function=_M3EmbedFn(model),
        metadata={"hnsw:space": "cosine"},
    )
    stale = col.get(limit=1000000)["ids"]
    if stale:
        col.delete(ids=stale)  # 幂等：先清再写

    # 手动主线程编码（Chroma 内部线程调 ef 全量编码 1.2 万条会死锁——
    # 实测 CPU 停滞），再分批显式 add(embeddings=...)
    # 一次编码同时产出 dense（Chroma）+ sparse（B3 用，pickle 落盘）
    # 优化：按内容长度排序（batch 内 padding 最小化——4GB 显存下
    # 长文本参差 batch 会让 GPU 算力浪费 + CUDA 内存抖动，实测慢 3 倍）
    # 止损：跳过父块（is_parent）——长文本（1500 字）在 4GB 显存下每批
    # 6-10s（实测 3826 块 23 分钟无进展），而语义路对比的核心是子块
    # （覆盖 90% 对比价值；父块缺失让 m3 侧略吃亏——若仍胜出结论更可信）
    print(f"  主线程编码全库（跳过 {sum(1 for d in all_docs if d['metadata'].get('is_parent'))} 个父块）…", flush=True)
    all_docs = [d for d in all_docs if not d["metadata"].get("is_parent")]
    ordered = sorted(enumerate(all_docs), key=lambda e: len(e[1]["content"]))
    embeddings: list[list[float]] = [None] * len(all_docs)  # 按原序存放
    sparse_map: dict[str, dict[int, float]] = {}
    BATCH = 32  # fp16 后显存减半，batch 32 稳
    for start in range(0, len(ordered), BATCH):
        chunk = [d for _, d in ordered[start:start + BATCH]]
        out = model.encode([d["content"] for d in chunk])
        embs = out.dense if hasattr(out, "dense") else out
        for (idx, _), v in zip(ordered[start:start + BATCH], embs):
            embeddings[idx] = v.tolist()
        done = start + BATCH
        if done % 2048 == 0:
            print(f"  编码 {done}/{len(all_docs)}…", flush=True)

    for i in range(0, len(all_docs), 500):
        chunk = all_docs[i:i + 500]
        col.add(
            ids=[d["id"] for d in chunk],
            documents=[d["content"] for d in chunk],
            metadatas=[d.get("metadata", {}) for d in chunk],
            embeddings=embeddings[i:i + 500],
        )

    return col, sparse_map, model


def _eval_sparse(sparse_map: dict, model, cases: list[dict],
                 source_of: dict[str, str]) -> dict:
    """bge-m3 稀疏向量（lexical）检索——query sparse × 文档 sparse 点积

    用倒排索引（token → [(doc_id, weight)]）实现，查询只遍历命中词项。
    对比对象：现状 BM25 路（jieba 分词 + rank_bm25）——同一评测集。
    """
    # 倒排: token_id → [(doc_id, weight)]
    inverted: dict[int, list[tuple[str, float]]] = {}
    for doc_id, weights in sparse_map.items():
        for tok, w in weights.items():
            inverted.setdefault(tok, []).append((doc_id, w))

    def _retrieve(q: str) -> list[str]:
        out = model.encode([q])
        q_sparse = out.sparse[0] if hasattr(out, "sparse") else out[0].sparse
        q_weights = dict(zip(q_sparse.indices.tolist(), q_sparse.weights.tolist()))
        scores: dict[str, float] = {}
        for tok, qw in q_weights.items():
            for doc_id, dw in inverted.get(tok, []):
                scores[doc_id] = scores.get(doc_id, 0.0) + qw * dw
        ranked = sorted(scores, key=scores.get, reverse=True)
        return ranked[:8]

    n_hit = n_total = 0
    mrr_sum = 0.0
    for case in cases:
        q = case["query"]
        history = case.get("history", [])
        query = f"{history[0]} {q}" if history else q
        expected = case["expected_sources"]
        if not expected:
            continue
        src_list = [source_of[cid] for cid in _retrieve(query) if cid in source_of]
        hit = [e for e in expected if e in src_list]
        if any(w in q for w in LIST_WORDS):
            ok = len(hit) > 0
        else:
            ok = len(hit) == len(expected)
        n_hit += int(ok)
        n_total += 1
        for rank, src in enumerate(src_list, start=1):
            if src in expected:
                mrr_sum += 1.0 / rank
                break
    return {
        "recall": round(n_hit / n_total, 4) if n_total else 1.0,
        "mrr": round(mrr_sum / n_total, 4) if n_total else 0.0,
        "n": n_total,
    }


def _eval_semantic(col, cases: list[dict], label: str) -> dict:
    """semantic 单路评测（Retrieval 接口兼容）"""
    n_hit = n_total = 0
    mrr_sum = 0.0

    def _retrieve(q: str):
        results = col.query(query_texts=[q], n_results=30)
        out = []
        for i in range(min(8, len(results["ids"][0]))):
            out.append({"source": (results["metadatas"][0][i] or {}).get("source", "")})
        return out

    t0 = time.perf_counter()
    for case in cases:
        q = case["query"]
        history = case.get("history", [])
        query = f"{history[0]} {q}" if history else q
        expected = case["expected_sources"]
        if not expected:
            continue  # 图谱直查类跳判（semantic 单路不覆盖）
        src_list = [r["source"] for r in _retrieve(query)]
        hit = [e for e in expected if e in src_list]
        if any(w in q for w in LIST_WORDS):
            ok = len(hit) > 0
        else:
            ok = len(hit) == len(expected)
        n_hit += int(ok)
        n_total += 1
        for rank, r in enumerate(src_list, start=1):
            if r in expected:
                mrr_sum += 1.0 / rank
                break
    elapsed = time.perf_counter() - t0
    return {
        "label": label,
        "recall": round(n_hit / n_total, 4) if n_total else 1.0,
        "mrr": round(mrr_sum / n_total, 4) if n_total else 0.0,
        "n": n_total,
        "avg_ms": round(elapsed / max(n_total, 1) * 1000),
    }


def main():
    cases = load_cases()
    all_docs = container.vector.get_all_documents()
    print(f"全库 {len(all_docs)} 块 | 核心集 {len(cases)} 题（图谱直查类自动跳判）", flush=True)

    # 基线：现状 bge-small semantic 路（生产 collection）
    t0 = time.perf_counter()
    base = _eval_semantic(container.vector.collection, cases, "bge-small（现状）")
    base["avg_ms"] = round((time.perf_counter() - t0) / max(base["n"], 1) * 1000)

    # bge-m3：重 embedding 独立 collection
    print("bge-m3 模型加载 + 全库重 embedding（CPU 约 75 分钟，一次性）…", flush=True)
    m3_col, sparse_map, model = _build_m3_collection(all_docs)
    t0 = time.perf_counter()
    m3 = _eval_semantic(m3_col, cases, "bge-m3")
    m3["avg_ms"] = round((time.perf_counter() - t0) / max(m3["n"], 1) * 1000)



    print(f"\n{'配置':<16} Recall@8   MRR     用例数   耗时/题")
    for r in (base, m3):
        print(f"{r['label']:<16} {r['recall']:.1%}   {r['mrr']:.4f}   {r['n']:<8} {r['avg_ms']}ms")
    delta_recall = m3["recall"] - base["recall"]
    delta_mrr = m3["mrr"] - base["mrr"]
    print(f"\nbge-m3 增量: Recall {delta_recall:+.1%} | MRR {delta_mrr:+.4f}")

    out = REPORTS_DIR / f"bge_m3_compare_{int(time.time())}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"base": base, "m3": m3, "delta": {
        "recall": delta_recall, "mrr": delta_mrr}}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"报告: {out}")


if __name__ == "__main__":
    main()
