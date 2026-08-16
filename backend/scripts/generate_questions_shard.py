# -*- coding: utf-8 -*-
"""分片问题生成（并行加速）——多进程调 LLM 无 Chroma 锁冲突

用法: python scripts/generate_questions_shard.py --shard=0 --total=3
处理 docs[shard::total] 的 chunk，问题写入 /tmp/questions_shard_{shard}.json。
全部 shard 完成后用 scripts/import_questions_shard.py 导入 Chroma。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.config import settings  # noqa: E402
from core.di import container  # noqa: E402
from retrieval.hypothesis import (  # noqa: E402
    generate_questions_for_batch,
    parse_question_results,
)

OUT_DIR = Path("/tmp") if sys.platform != "win32" else Path("C:/Users/GR/AppData/Local/Temp")


def parse_args():
    args = {}
    for a in sys.argv[1:]:
        if a.startswith("--shard="):
            args["shard"] = int(a.split("=")[1])
        elif a.startswith("--total="):
            args["total"] = int(a.split("=")[1])
        elif a.startswith("--limit="):
            args["limit"] = int(a.split("=")[1])
        elif a == "--resume":
            args["resume"] = True
    assert "shard" in args and "total" in args, "需要 --shard=N --total=M"
    return args


async def main():
    args = parse_args()
    shard, total = args["shard"], args["total"]
    limit = args.get("limit")
    out = OUT_DIR / f"questions_shard_{shard}.json"

    docs = container.vector.get_all_documents()
    # 父块（is_parent）内容 = 子块拼接，假设问题与子块重复——跳过，
    # 避免重复问题稀释 Q-to-Q 检索（蓝图第 2 步，父子分块引入后）
    docs = [d for d in docs if not d["metadata"].get("is_parent")]

    def _shard_of(d: dict) -> int:
        # 按 chunk id 哈希分片（与导入基准一致；索引分片在数据顺序变化时不稳定）
        return int(d["id"].replace("-", ""), 16) % total

    docs = [d for d in docs if _shard_of(d) == shard]
    if limit:
        docs = docs[:limit]
    # 断点续跑：跳过已写入 JSON 的 chunk（服务波动时 --resume 重启不浪费）
    existing: dict[str, list[str]] = {}
    if args.get("resume") and out.exists():
        existing = json.loads(out.read_text(encoding="utf-8"))
        docs = [d for d in docs if d["id"] not in existing]
        print(f"续跑: 跳过已生成 {len(existing)} chunk，剩余 {len(docs)}")
    print(f"Shard {shard}/{total}: {len(docs)} chunks")

    batch_size = settings.hypothesis_batch_size
    results: dict[str, list[str]] = {}
    done = 0
    for start in range(0, len(docs), batch_size):
        batch = docs[start : start + batch_size]
        try:
            mapped = await generate_questions_for_batch(container.llm, batch)
            results.update(mapped)
        except Exception as e:
            print(f"batch {start} 失败: {e}", flush=True)
        done += len(batch)
        if done % 300 == 0:
            print(f"  进度: {done}/{len(docs)}", flush=True)

    out = OUT_DIR / f"questions_shard_{shard}.json"
    # 合并已有进度写盘（--resume 续跑时保留旧结果，避免覆盖丢失）
    merged = {**existing, **results}
    out.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    print(f"完成: 本次 {len(results)} + 已有 {len(existing)} = {len(merged)} chunks → {out}")


if __name__ == "__main__":
    asyncio.run(main())
