# -*- coding: utf-8 -*-
"""P2 统一 ingest 管道测试——P1-C 契约校验、注册表流程、BaseIngestor.load 幂等

覆盖:
- validate_source: 三种合法形态（{域}-{实体} / {域}-{实体}#图 / {timestamp}_{file}）与非法形态
- 注册表: register / get_ingestor / ingestors / run（fake ingestor，dry-run 不触真库）
- load 幂等: 临时 Chroma（tmp_path + 假 embedding）验证重复入库不产生重复块
- load 附属行为: image_index 映射表更新、进度回调、chunk_type 缺失补齐
"""
import json

import pytest

from ingestion.application.ingest_base import BaseIngestor, RawSource
from ingestion.application.ingest_service import (
    IngestorNotFoundError,
    get_ingestor,
    ingestors,
    register,
    run,
    validate_source,
)

# ── P1-C 数据契约校验 ─────────────────────────────

@pytest.mark.parametrize("source", [
    "青铜-叩鼎",                        # 文本块: 域-实体
    "河南博物院-妇好墓玉龙",              # 多字域
    "宣德-青花梅瓶",                     # 瓷器风格
    "窑口-宣德",                        # 窑口域
    "河南博物院-妇好墓玉龙#图",           # 图片块: #图 后缀
    "青铜-素面弦纹鼎#图",
    "1234567890_a.pdf",                 # 上传文档: 时间戳前缀
    "1786074778_妇好鸮尊介绍.pdf",        # 时间戳 + 中文文件名
])
def test_validate_source_valid(source):
    assert validate_source(source) is True


@pytest.mark.parametrize("source", [
    "乱写的source",          # 无 连字符/下划线 结构
    "Bronze-ding",           # 域与实体均无汉字（纯拉丁词）
    "",                      # 空串
    "青铜-",                 # 只有域没有实体
    "-叩鼎",                 # 只有实体没有域
    "青铜 叩鼎",              # 含空格
    "青铜--叩鼎",             # 双连字符
    "青铜-叩鼎#图图",          # 后缀不完整
    "1234567890",            # 缺 _file 部分
    "a.pdf_123",             # 时间戳不在前缀位置
    None,
])
def test_validate_source_invalid(source):
    assert validate_source(source) is False


# ── 注册表 + run 流程 ─────────────────────────────

class FakeIngestor(BaseIngestor):
    """测试用假 ingestor——scan 返回 2 个合法 + 1 个非法 source；load 为记录桩"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.load_calls = 0
        self.loaded_chunks: list[dict] = []

    def scan(self) -> list[RawSource]:
        return [
            RawSource(source="青铜-叩鼎", text="叩鼎是商代青铜食器。"),
            RawSource(source="河南博物院-妇好墓玉龙", text="妇好墓玉龙形制优美。"),
            RawSource(source="乱写的source", text="会被 P1-C 契约过滤掉"),
        ]

    def build_chunks(self, raw: RawSource) -> list[dict]:
        return [{
            "chunk_id": f"chunk-{raw.source}",
            "content": raw.text,
            "metadata": {"source": raw.source, "chunk_type": "text"},
        }]

    def load(self, chunks: list[dict], progress=None) -> int:
        self.load_calls += 1
        self.loaded_chunks = list(chunks)
        return len(chunks)


def test_register_get_ingestors():
    register("fake-class", FakeIngestor)
    assert "fake-class" in ingestors()

    ing = get_ingestor("fake-class")
    assert isinstance(ing, FakeIngestor)
    # 类注册: 每次 get 新建实例
    assert get_ingestor("fake-class") is not ing


def test_get_ingestor_unknown_raises():
    with pytest.raises(IngestorNotFoundError):
        get_ingestor("不存在的-ingestor")


def test_run_dry_run_skips_load_and_filters_invalid():
    ing = FakeIngestor()
    register("fake-inst-dry", ing)

    stats = run("fake-inst-dry", dry_run=True)

    assert stats["scanned"] == 3
    assert stats["sources"] == 2            # 非法 source 被契约过滤
    assert stats["chunks"] == 2
    assert stats["invalid"] == ["乱写的source"]
    assert stats["dry_run"] is True
    assert stats["loaded"] is False
    assert ing.load_calls == 0              # dry-run 不调 load（不触真库）


def test_run_calls_load_with_ctor_opts():
    ing = FakeIngestor()
    register("fake-inst-load", ing)

    stats = run("fake-inst-load")           # 非 dry-run

    assert stats["loaded"] is True
    assert ing.load_calls == 1
    assert len(ing.loaded_chunks) == 2
    assert {c["metadata"]["source"] for c in ing.loaded_chunks} == {
        "青铜-叩鼎", "河南博物院-妇好墓玉龙"}


def test_run_limit():
    ing = FakeIngestor()
    register("fake-inst-limit", ing)

    stats = run("fake-inst-limit", dry_run=True, limit=1)

    assert stats["scanned"] == 3
    assert stats["sources"] == 1
    assert stats["chunks"] == 1


# ── BaseIngestor.load 幂等（临时 Chroma + 假 embedding）──

class FakeEmbed:
    """假 embedding——固定向量，避免真实模型加载/下载"""

    def __call__(self, input):
        return [[0.1, 0.2, 0.3]] * len(input)

    def embed_query(self, input):
        return self.__call__([input])

    def embed_documents(self, input):
        return self.__call__(input)

    def name(self):
        return "fake-embed"


class ChunkIngestor(BaseIngestor):
    """固定块内容的假 ingestor——走基类真实 load 逻辑（先删后写 + 映射表）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._chunks = [{
            "chunk_id": "fixed-1",
            "content": "叩鼎器身铸饕餮纹，出土于殷墟。",
            "metadata": {"source": "青铜-叩鼎", "chunk_type": "text"},
        }]

    def scan(self) -> list[RawSource]:
        return [RawSource(source="青铜-叩鼎", text="叩鼎器身铸饕餮纹，出土于殷墟。")]

    def build_chunks(self, raw: RawSource) -> list[dict]:
        return self._chunks


@pytest.fixture
def tmp_store(tmp_path):
    from ingestion.infrastructure.chroma_store import ChromaStore

    store = ChromaStore(
        persist_dir=str(tmp_path / "chroma"),
        embedding_function=FakeEmbed(),
        collection_name="documents",
    )
    return store


@pytest.fixture
def tmp_ingestor(tmp_path, tmp_store):
    return ChunkIngestor(
        vector=tmp_store,
        image_index_path=str(tmp_path / "image_index.json"),
    )


def test_load_idempotent(tmp_ingestor, tmp_store):
    """同一批块重复 load: 先删后写，不产生重复块"""
    chunks = tmp_ingestor.build_chunks(tmp_ingestor.scan()[0])

    first = tmp_ingestor.load(chunks)
    second = tmp_ingestor.load(chunks)

    assert first == 1
    assert second == 1
    assert tmp_store.count() == 1
    assert tmp_store.count_by_source("青铜-叩鼎") == 1
    assert tmp_store.count_by_source("青铜-叩鼎#图") == 0


def test_load_updates_image_index(tmp_path, tmp_store):
    """图片块 metadata.image_path(/api/images/ 前缀) → image_index.json 幂等合并"""
    img_path = tmp_path / "image_index.json"
    ing = ChunkIngestor(vector=tmp_store, image_index_path=str(img_path))
    chunks = [
        {"chunk_id": "img-1", "content": "【图片】叩鼎全形",
         "metadata": {"source": "青铜-叩鼎#图", "chunk_type": "image",
                      "image_path": "/api/images/bronze/101014.png"}},
        {"chunk_id": "txt-1", "content": "叩鼎铭文",
         "metadata": {"source": "青铜-叩鼎", "chunk_type": "text"}},
        {"chunk_id": "up-1", "content": "【文档图片】上传图",
         "metadata": {"source": "1234567890_a.pdf", "chunk_type": "image",
                      "image_path": "/api/uploads/1234567890_a.pdf.images/fig_1.png"}},
    ]
    ing.load(chunks)

    index = json.loads(img_path.read_text(encoding="utf-8"))
    # 只登记 /api/images/ 前缀（上传文档 /api/uploads/ 不进映射表）
    assert "1234567890_a.pdf" not in index
    assert index["青铜-叩鼎#图"] == {"primary": "bronze/101014.png",
                                     "images": ["bronze/101014.png"]}
    # 幂等: 重复 load 映射表内容不变
    ing.load(chunks)
    assert json.loads(img_path.read_text(encoding="utf-8")) == index


def test_load_progress_callback(tmp_path, tmp_store):
    """进度回调 (done, total) 每批触发一次，最终到达总数"""
    ing = ChunkIngestor(vector=tmp_store, image_index_path=str(tmp_path / "image_index.json"))
    chunks = [{
        "chunk_id": f"c-{i}",
        "content": f"块{i}",
        "metadata": {"source": "青铜-叩鼎", "chunk_type": "text"},
    } for i in range(25)]  # 25 块 > 批大小 20，触发两次回调

    calls = []
    ing.load(chunks, progress=lambda done, total: calls.append((done, total)))

    assert calls == [(20, 25), (25, 25)]


def test_load_defaults_chunk_type_text(tmp_path, tmp_store):
    """缺失 chunk_type 的块按 P1-A 契约补齐为 text（新入库统一由管道标记）"""
    ing = ChunkIngestor(vector=tmp_store, image_index_path=str(tmp_path / "image_index.json"))
    chunks = [{
        "chunk_id": "no-type",
        "content": "没有类型标记的旧式块",
        "metadata": {"source": "青铜-叩鼎"},
    }]
    ing.load(chunks)

    docs = tmp_store.get_all_documents()
    assert len(docs) == 1
    assert docs[0]["metadata"]["chunk_type"] == "text"


def test_load_skips_chunks_without_source(tmp_path, tmp_store):
    """无 source 的块违反 P1-C 契约，load 跳过不入库"""
    ing = ChunkIngestor(vector=tmp_store, image_index_path=str(tmp_path / "image_index.json"))
    chunks = [
        {"chunk_id": "ok-1", "content": "合法块",
         "metadata": {"source": "青铜-叩鼎", "chunk_type": "text"}},
        {"chunk_id": "bad-1", "content": "无 source 的块", "metadata": {}},
        {"chunk_id": "bad-2", "content": "无 metadata 的块", "metadata": None},
        "不是 dict 的块",
    ]
    ing.load(chunks)

    assert tmp_store.count() == 1
    assert tmp_store.get_all_documents()[0]["id"] == "ok-1"
