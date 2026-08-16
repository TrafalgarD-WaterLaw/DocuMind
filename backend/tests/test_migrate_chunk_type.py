"""P1-A 迁移脚本测试——chunk_type 补全（不重 embedding，where 精确匹配不受影响）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import chromadb  # noqa: E402
import pytest  # noqa: E402

from migrate_chunk_type import migrate  # noqa: E402


def _make_collection(tmp_path: Path, chroma: chromadb.ClientAPI) -> tuple[object, list[str]]:
    """建临时 collection：2 条无 type 文本块 + 1 条 image 块"""
    col = chroma.get_or_create_collection("t_migrate")
    col.add(
        ids=["t1", "t2", "t3"],
        documents=["商代青铜器纹饰", "妇好鸮尊出土于殷墟", "【图片】鼎的照片"],
        metadatas=[
            {"source": "青铜-妇好鸮尊"},
            {"source": "青铜-妇好鸮尊"},
            {"source": "青铜-妇好鸮尊#图", "chunk_type": "image",
             "image_path": "/api/images/x.png"},
        ],
        embeddings=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],  # 假向量，验证不重算
    )
    return col


def test_migrate_backfills_text_chunk_type(tmp_path):
    chroma = chromadb.PersistentClient(path=str(tmp_path))
    col = _make_collection(tmp_path, chroma)

    result = migrate(persist_dir=str(tmp_path), collection_name="t_migrate")

    assert result["migrated"] == 2  # 只补文本块
    data = col.get(include=["metadatas"])
    by_id = {i: m for i, m in zip(data["ids"], data["metadatas"])}
    assert by_id["t1"]["chunk_type"] == "text"
    assert by_id["t2"]["chunk_type"] == "text"
    assert by_id["t3"]["chunk_type"] == "image"  # 已有标记不动


def test_migrate_idempotent(tmp_path):
    chroma = chromadb.PersistentClient(path=str(tmp_path))
    _make_collection(tmp_path, chroma)

    first = migrate(persist_dir=str(tmp_path), collection_name="t_migrate")
    second = migrate(persist_dir=str(tmp_path), collection_name="t_migrate")

    assert first["migrated"] == 2
    assert second["migrated"] == 0


def test_migrate_preserves_embeddings(tmp_path):
    """只改 metadata——embedding 保持不变（update 不带 embeddings 时 Chroma 保留原值）"""
    chroma = chromadb.PersistentClient(path=str(tmp_path))
    col = _make_collection(tmp_path, chroma)
    before = col.get(ids=["t1"], include=["embeddings"])["embeddings"][0]

    migrate(persist_dir=str(tmp_path), collection_name="t_migrate")

    after = col.get(ids=["t1"], include=["embeddings"])["embeddings"][0]
    assert list(before) == list(after)


def test_migrate_image_where_still_exact(tmp_path):
    """迁移后 where={chunk_type: image} 精确匹配图片块——检索直检通道不受影响"""
    chroma = chromadb.PersistentClient(path=str(tmp_path))
    _make_collection(tmp_path, chroma)
    migrate(persist_dir=str(tmp_path), collection_name="t_migrate")

    hits = chroma.get_or_create_collection("t_migrate").get(
        where={"chunk_type": "image"}, include=["metadatas"]
    )
    assert hits["ids"] == ["t3"]
