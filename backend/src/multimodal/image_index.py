"""图片映射表服务——source → 图片 URL

数据源: src/data/image_index.json（scripts/import_dataset_images.py 生成）
语义: 检索命中文本块时，按 source 查映射表，把该器物的图片随 sources 返回，
     前端证据链展示。图片本身不进向量库。

FileBackedImageIndex 实例状态（容器装配 core.di.container.image_index;
ingest/测试注入临时路径实例）;模块函数为无状态委托入口（DDD 蓝图规则）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "image_index.json"


def _to_url(rel: str) -> str:
    """映射表路径 → /api/images/ URL

    映射表存的相对路径不带 images/ 前缀（如 bronze/101014.png），
    这里统一剥离可能残留的 images/ 前缀，避免拼出 /api/images/images/… 双前缀。
    """
    return f"/api/images/{rel}" if not rel.startswith("images/") else f"/api/images/{rel[7:]}"


class FileBackedImageIndex:
    """source → 图片 URL 映射表——mtime 失效缓存 + 注入路径（实例状态）

    进程内缓存：懒加载 + mtime 失效（M6——运行期 ingest 写入/删除后
    不必重启服务即可看到最新映射）。
    """

    def __init__(self, index_path: Path | None = None) -> None:
        self._path = index_path or INDEX_PATH
        self._cache: dict | None = None
        self._last_mtime: float | None = None

    def _load(self) -> dict:
        """加载映射表（懒加载 + mtime 失效检查）"""
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            mtime = None
        if self._cache is not None and mtime == self._last_mtime:
            return self._cache
        try:
            if self._path.exists():
                self._cache = json.loads(self._path.read_text(encoding="utf-8"))
                self._last_mtime = mtime
                return self._cache
        except Exception as e:
            logger.warning(f"image_index 加载失败: {e}")
        self._cache = {}
        self._last_mtime = mtime
        return self._cache

    def load_all(self) -> dict:
        """返回完整映射表（source → {primary, images}）"""
        return self._load()

    def get_images_for_source(self, source: str) -> list[str]:
        """source → 带 /api/images/ 前缀的图片 URL 列表；无图返回 []"""
        entry = self._load().get(source)
        if not entry:
            return []
        files = entry.get("images") or []
        return [_to_url(f) for f in files if f]

    def upsert(self, source: str, files: list[str]) -> None:
        """合并写入映射条目（source → {primary, images}，幂等覆盖）

        先写盘后改内存（与 remove 同规则）——写盘失败时缓存不动。
        """
        data: dict = {}
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"image_index 读取失败，按空表合并: {e}")
        data[source] = {"primary": files[0], "images": files}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._cache = data
            self._last_mtime = self._path.stat().st_mtime
        except Exception as e:
            logger.warning(f"image_index 合并失败（缓存未改动）: {e}")
            return
        logger.info(f"image_index 合并条目: {source[:24]}（{len(files)} 图）")

    def remove(self, source: str) -> None:
        """删除文档时同步移除映射条目（M6——删除后映射不残留）

        先写盘后改内存——写盘失败时缓存不动，避免内存-磁盘分叉
        （写盘失败时缓存不动）。
        """
        data = self._load()
        if source not in data:
            return
        new_data = {k: v for k, v in data.items() if k != source}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._cache = new_data
            self._last_mtime = self._path.stat().st_mtime
        except Exception as e:
            logger.warning(f"image_index 移除失败（缓存未改动）: {e}")
            return
        logger.info(f"image_index 移除条目: {source[:24]}")


def _default_index() -> FileBackedImageIndex:
    """组合根装配的默认索引——缓存状态归容器,委托函数无全局状态"""
    from core.di import container

    return container.image_index


def load_image_index() -> dict:
    return _default_index().load_all()


def get_images_for_source(source: str) -> list[str]:
    return _default_index().get_images_for_source(source)


def upsert(source: str, files: list[str]) -> None:
    _default_index().upsert(source, files)


def remove_source(source: str) -> None:
    _default_index().remove(source)
