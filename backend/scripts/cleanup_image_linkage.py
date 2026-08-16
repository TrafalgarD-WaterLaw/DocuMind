# -*- coding: utf-8 -*-
"""图片链路数据一致性清理——脏数据避免（删除文档/重命名后映射残留）

清理三类不一致:
  A. image_index.json 孤立键——文本库重命名/删除后，映射表还挂旧键
     （乱码旧键按 cleanup_garbled_sources 的改名规则同步；纯删除的键移除）
  B. clip_images 索引残留——乱码 source 清理后，CLIP 索引仍含其图片
     （CLIP 图找文会返回已删 source 的图 → 脏数据污染图找文）
  C. uploads 残留文件——chroma 无对应 source 的 PDF/.images（历史测试残留）

幂等: 每次以 chroma 现状为准重新计算差异。
用法: python scripts/cleanup_image_linkage.py [--apply]
"""
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import chromadb  # noqa: E402

CHROMA_DIR = Path(__file__).resolve().parent.parent / "src" / "data" / "chroma"
DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "data"
HANGUL = re.compile(r"[가-힣]+")


def has_hangul(s: str) -> bool:
    return bool(HANGUL.search(s))


def new_name_for(s: str) -> str | None:
    """同 cleanup_garbled_sources 的改名规则（仅返回新名，无法修复返回 None）"""
    if not has_hangul(s):
        return s
    main = re.split(r"[（(]", s, maxsplit=1)[0].strip()
    if not has_hangul(main):
        return main
    for m in re.finditer(r"[（(]([^）)]+)[）)]", s):
        inner = re.sub(r"^(原名|原稱)", "", m.group(1))
        for part in re.split(r"[、，,/]", inner):
            part = part.strip()
            if not has_hangul(part) and re.search(r"[一-鿿]", part):
                return f"{main.split('-')[0]}-{part}"
    return None


def main():
    apply = "--apply" in sys.argv
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # chroma 现状 source 集
    doc = client.get_collection("documents")
    doc_sources = {m.get("source", "") for m in
                   doc.get(limit=1000000, include=["metadatas"])["metadatas"]}

    # ── A. image_index.json 孤立键同步 ──
    idx_path = DATA_DIR / "image_index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    renamed, dropped = 0, 0
    for key in list(idx.keys()):
        if key in doc_sources:
            continue
        new = new_name_for(key)
        if new and new in doc_sources:
            idx[new] = idx.pop(key)
            renamed += 1
        else:
            idx.pop(key)
            dropped += 1
    if renamed or dropped:
        print(f"A. image_index.json: 重命名键 {renamed}，删除孤立键 {dropped}")
        if apply:
            idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            print("   已写入")
    else:
        print("A. image_index.json: 无孤立键")

    # ── B. clip_images 残留清理（source 不在 documents 即脏） ──
    try:
        clip = client.get_collection("clip_images")
    except Exception:
        clip = None
    if clip is not None:
        cres = clip.get(limit=1000000, include=["metadatas"])
        drop_ids = [
            cid for cid, m in zip(cres["ids"], cres["metadatas"])
            if m.get("source", "") not in doc_sources
            and m.get("source", "").removesuffix("#图") not in doc_sources
        ]
        print(f"B. clip_images: 待清理 {len(drop_ids)} 条（source 不在库）")
        if drop_ids and apply:
            clip.delete(ids=drop_ids)
            print(f"   已删除")
    else:
        print("B. clip_images: 索引不存在，跳过")

    # ── C. uploads 残留文件清理（chroma 无对应 source） ──
    removed_files = 0
    if (DATA_DIR / "uploads").exists():
        for p in sorted((DATA_DIR / "uploads").iterdir()):
            if p.is_file() and p.name.endswith(".pdf"):
                src = p.name.removesuffix(".pdf")
                if src not in doc_sources:
                    print(f"C. 残留 PDF: {p.name[:40]}")
                    if apply:
                        p.unlink()
                        removed_files += 1
            elif p.is_dir() and p.name.endswith(".images"):
                src = p.name.removesuffix(".images")
                if src not in doc_sources:
                    print(f"C. 残留图片目录: {p.name[:40]}")
                    if apply:
                        shutil.rmtree(p)
                        removed_files += 1
        if apply:
            print(f"C. 已删除文件/目录: {removed_files}")
    else:
        print("C. uploads 目录不存在")

    if not apply:
        print("\n[dry-run] 未写入。加 --apply 执行。")


if __name__ == "__main__":
    main()
