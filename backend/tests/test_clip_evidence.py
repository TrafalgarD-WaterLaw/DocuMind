"""CLIP 图片证据链测试——视觉命中图注块进回答上下文（独立证据链）"""
import time

from core.config import settings
from core.tracing import RetrievalTrace
from conversation.application.quick_answer import QuickAnswerService
from multimodal.evidence import collect_image_evidence


class FakeLLM:
    """记录最后一次 user 消息（供断言拼接后的上下文）"""

    def __init__(self):
        self.last_user = ""

    def build_messages(self, system, user):
        self.last_user = user
        return [{"role": "user", "content": user}]

    async def chat(self, messages, **kwargs):
        return "good"

    async def chat_stream(self, messages, **kwargs):
        for ch in ["好"]:
            yield ch


class FakeDocStore:
    """模拟 ChromaStore.get_by_where（返回顺序故意乱序，验证稳定排序）

    解析 {"$and": [{source: $in}, {chunk_type}]}（Chroma where 顶层单
    operator 语法），语义与真实实现一致。
    """

    def __init__(self, blocks: list[dict]):
        self.blocks = blocks

    def get_by_where(self, where, limit=50):
        conds = where.get("$and") if "$and" in where else [where]

        def _match(b):
            meta = b.get("metadata", {})
            for cond in conds:
                if "source" in cond and meta.get("source") not in cond["source"].get("$in", []):
                    return False
                if "chunk_type" in cond and meta.get("chunk_type") != cond["chunk_type"]:
                    return False
            return True

        return [b for b in self.blocks if _match(b)][:limit]


class FakeRetriever:
    def __init__(self, doc_store=None):
        self.doc_store = doc_store

    async def retrieve(self, query, **kwargs):
        return [
            {"id": "c1", "content": "商代青铜鼎。", "source": "青铜-司母戊鼎",
             "paths": ["semantic"], "metadata": {"source": "青铜-司母戊鼎"}},
        ]


def _block(bid, source, content, chunk_type="image"):
    return {"id": bid, "content": content,
            "metadata": {"source": source, "chunk_type": chunk_type}}


BLOCKS = [
    _block("im1", "青铜-重鼎#图", "【图片·图1】重鼎，兽面纹饰"),
    _block("im2", "青铜-史父丁鼎#图", "【图片·图2】史父丁鼎"),
    _block("im3", "河南博物院-妇好鸮尊#图", "【图片·图1】妇好鸮尊，鸮形"),
    # 文本块:source 不带 #图（河南数据集命名）——图片证据不得误捞
    _block("txt1", "河南博物院-妇好鸮尊", "妇好鸮尊正文……", chunk_type="text"),
]


async def test_image_evidence_skips_text_blocks(monkeypatch):
    """chunk_type=image 过滤:同 source 的文本块（不带 #图）不混入图片证据"""
    monkeypatch.setattr(settings, "clip_evidence_max_blocks", 6)
    # 数据集命名:文本块 source 与 clip_images 一致（不带 #图）
    evs = await collect_image_evidence(
        FakeDocStore(BLOCKS), {"河南博物院-妇好鸮尊": ["/api/images/..."]},
    )
    assert [e["id"] for e in evs] == ["im3"]


# ── collect_image_evidence（multimodal/evidence.py，从 quick.py 抽出）──

async def test_image_evidence_ordered_by_clip_hits(monkeypatch):
    """返回顺序 = 视觉命中顺序（Chroma where 乱序 → 按 key 稳定排序）"""
    monkeypatch.setattr(settings, "clip_evidence_max_blocks", 6)
    clip = {
        "青铜-史父丁鼎#图": ["/api/images/..."],
        "青铜-重鼎#图": ["/api/images/..."],
    }
    evs = await collect_image_evidence(FakeDocStore(BLOCKS), clip)
    assert [e["id"] for e in evs] == ["im2", "im1"]


async def test_image_evidence_dataset_key_without_hash(monkeypatch):
    """数据集图片 key 不带 #图（import_clip_images 命名）→ 也能查到图注块

    clip_images 索引里数据集图片 source 无 #图，documents 图注块带 #图——
    两种 key 都要能映射到图注块。
    """
    monkeypatch.setattr(settings, "clip_evidence_max_blocks", 6)
    evs = await collect_image_evidence(
        FakeDocStore(BLOCKS), {"青铜-重鼎": ["/api/images/..."]},
    )
    assert [e["id"] for e in evs] == ["im1"]


async def test_image_evidence_max_blocks(monkeypatch):
    """超过上限截断（图注块短，上限防 token 膨胀）"""
    monkeypatch.setattr(settings, "clip_evidence_max_blocks", 1)
    clip = {"青铜-重鼎#图": ["x"], "青铜-史父丁鼎#图": ["x"]}
    evs = await collect_image_evidence(FakeDocStore(BLOCKS), clip)
    assert len(evs) == 1


async def test_image_evidence_exclude_duplicate_ids():
    """与文本证据重复的块（图片块直检通道可能已带）排除"""
    clip = {"青铜-重鼎#图": ["x"]}
    evs = await collect_image_evidence(FakeDocStore(BLOCKS), clip, exclude_ids={"im1"})
    assert evs == []


async def test_image_evidence_no_doc_store():
    """无 doc_store（如测试 FakeRetriever）→ 空，不影响主链路"""
    assert await collect_image_evidence(None, {"青铜-重鼎#图": ["x"]}) == []
    assert await collect_image_evidence(None, {}) == []


async def test_image_evidence_store_error_degrades():
    """doc_store 抛异常 → 降级为空（不阻断回答）"""

    class _Boom:
        def get_by_where(self, where, limit=50):
            raise RuntimeError("chroma down")

    assert await collect_image_evidence(_Boom(), {"青铜-重鼎#图": ["x"]}) == []


# ── _generate_answer 拼装 ────────────────────────────

async def test_generate_answer_appends_image_evidence(monkeypatch):
    """图片证据续接编号追加进上下文（文本证据 [1] → 图注 [2]）"""
    monkeypatch.setattr(settings, "clip_evidence_max_blocks", 6)
    llm = FakeLLM()
    orch = QuickAnswerService(llm=llm, retriever=FakeRetriever(FakeDocStore(BLOCKS)))
    trace = RetrievalTrace(trace_id="t1", query="q")
    clip = {"青铜-重鼎#图": ["/api/images/bronze/xx.png"]}
    docs = [{"id": "c1", "content": "商代青铜鼎。", "source": "青铜-重鼎",
             "paths": ["semantic"], "metadata": {"source": "青铜-重鼎"}}]
    async for _ in orch._generate_answer(
        "重鼎是什么样的", None, docs, trace, time.time(), clip,
    ):
        pass
    assert "[2] 【图片·图1】重鼎" in llm.last_user
    assert "[1] 商代青铜鼎" in llm.last_user


async def test_generate_answer_without_clip_unchanged(monkeypatch):
    """不传 clip_by_source（分解路）→ 上下文与原行为一致，无图注追加"""
    monkeypatch.setattr(settings, "clip_evidence_max_blocks", 6)
    llm = FakeLLM()
    orch = QuickAnswerService(llm=llm, retriever=FakeRetriever(FakeDocStore(BLOCKS)))
    trace = RetrievalTrace(trace_id="t2", query="q")
    docs = [{"id": "c1", "content": "商代青铜鼎。", "source": "青铜-重鼎",
             "paths": ["semantic"], "metadata": {"source": "青铜-重鼎"}}]
    async for _ in orch._generate_answer(
        "重鼎是什么样的", None, docs, trace, time.time(),
    ):
        pass
    assert "【图片·图1】" not in llm.last_user
