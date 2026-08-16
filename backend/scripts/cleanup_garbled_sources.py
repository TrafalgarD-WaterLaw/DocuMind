# -*- coding: utf-8 -*-
"""清理韩文乱码 source——青铜域早期导入编码损坏（GBK 字符被解码成谚文音节）

损坏特征: source 名含韩文音节（U+AC00-D7A3），如「青铜-냻鼎」「青铜-썚鼎（髫鼎）」
影响: 实体锚定失效（名字无法精确匹配）、前端展示乱码、语义检索混入低质量命中

处理规则（按 source 主名/括号别名分类）:
  1. 主名干净、括号别名含乱码  → 重命名为主名（青铜-戍嗣子鼎（原名戍듟鼎）→ 青铜-戍嗣子鼎）
  2. 主名含乱码、括号有中文别名 → 重命名为第一个中文别名（青铜-썚鼎（髫鼎）→ 青铜-髫鼎）
  3. 主名含乱码、无中文别名     → 删除该块（单行简介，信息量低）
  content 首行标题同步替换（【西周】썚鼎（髫鼎）（青铜器）→【西周】髫鼎（青铜器））

幂等: 重跑时干净 source 不再匹配（韩文检测为入口）；dry-run 默认只统计不写入。
用法: python scripts/cleanup_garbled_sources.py [--apply] [--dry-run]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import chromadb  # noqa: E402

CHROMA_DIR = Path(__file__).resolve().parent.parent / "src" / "data" / "chroma"
HANGUL = re.compile(r"[가-힣]+")


def has_hangul(s: str) -> bool:
    return bool(HANGUL.search(s))


def extract_main(s: str) -> str:
    """主名 = 第一个中英文括号前的部分"""
    m = re.split(r"[（(]", s, maxsplit=1)
    return m[0].strip()


def extract_aliases(s: str) -> list[str]:
    """括号内的中文候选别名（去除 原名/原稱 前缀与乱码段）"""
    aliases: list[str] = []
    for m in re.finditer(r"[（(]([^）)]+)[）)]", s):
        inner = m.group(1)
        inner = re.sub(r"^(原名|原稱)", "", inner.strip())
        # 顿号/逗号/斜杠分隔的多别名取第一个中文段
        for part in re.split(r"[、，,/]", inner):
            part = part.strip()
            if not has_hangul(part) and re.search(r"[一-鿿]", part):
                aliases.append(part)
    return aliases


def classify(source: str) -> tuple[str, str, str | None]:
    """返回 (动作, 新名, 说明): action in {rename_main, rename_alias, delete, skip}"""
    main = extract_main(source)
    aliases = extract_aliases(source)
    if not has_hangul(source):
        return "skip", source, "干净"
    if not has_hangul(main):
        return "rename_main", main, "仅括号乱码→去别名"
    if aliases:
        return "rename_alias", f"{main.split('-')[0]}-{aliases[0]}", f"主名乱码→用别名 {aliases[0]}"
    return "delete", None, "主名乱码且无中文别名"


def rewrite_title(content: str, new_source: str) -> str:
    """content 首行标题替换为 新名（（青铜器））格式；无匹配则原样"""
    lines = content.split("\n")
    if not lines:
        return content
    title = lines[0]
    dynasty = re.match(r"^【([^】]*)】", title)
    if dynasty:
        lines[0] = f"【{dynasty.group(1)}】{new_source.split('-', 1)[-1]}（青铜器）"
        return "\n".join(lines)
    return content


def main():
    apply = "--apply" in sys.argv
    dry_run = "--dry-run" in sys.argv or not apply

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    col = client.get_collection("documents")
    # 必须取回 embeddings 原样回传——update 传 documents 不传 embeddings 时
    # Chroma 会用默认 ef 重新编码（维度不符报 512/384 错）
    res = col.get(limit=1000000, include=["metadatas", "documents", "embeddings"])
    ids = res["ids"]
    metas = res["metadatas"]
    docs = res["documents"]
    embeds = res["embeddings"]

    stats = {"rename_main": 0, "rename_alias": 0, "delete": 0, "skip": 0}
    examples: dict[str, list] = {"rename_main": [], "rename_alias": [], "delete": []}

    # 按 source 分组（同 source 的块统一处理）
    by_source: dict[str, list[int]] = {}
    for i, m in enumerate(metas):
        src = m.get("source", "")
        if has_hangul(src):
            by_source.setdefault(src, []).append(i)

    # 先分类存动作（真实 id 保存在 ids 原数组，不覆盖）
    actions: dict[str, tuple[str, str | None]] = {}
    to_delete: list[str] = []
    rename_batches: list[tuple[list[str], list[dict], list[str], list]] = []
    for src, idxs in sorted(by_source.items()):
        action, new_name, why = classify(src)
        actions[src] = (action, new_name)
        stats[action] += 1
        if action == "delete":
            to_delete.extend(ids[i] for i in idxs)
        elif action in ("rename_main", "rename_alias"):
            for i in idxs:
                metas[i]["source"] = new_name
                docs[i] = rewrite_title(docs[i], new_name)
            rename_batches.append(([ids[i] for i in idxs], [metas[i] for i in idxs],
                                   [docs[i] for i in idxs],
                                   [embeds[i] for i in idxs]))
        if action != "skip" and len(examples[action]) < 4:
            examples[action].append(f"{src} -> {new_name or '(删除)'} ({why})")

    print(f"乱码 source 总数: {len(by_source)}")
    print(f"  仅括号乱码→去别名: {stats['rename_main']}")
    print(f"  主名乱码→用中文别名: {stats['rename_alias']}")
    print(f"  删除（无别名）: {stats['delete']}")
    print()
    for action in ("rename_main", "rename_alias", "delete"):
        if examples[action]:
            print(f"── {action} 样例 ──")
            for e in examples[action]:
                print(f"  {e}")
            print()

    if to_delete:
        print(f"将删除块数: {len(to_delete)}")

    if dry_run:
        print("\n[dry-run] 未写入。加 --apply 执行。")
        return

    # ── 执行 ──
    if to_delete:
        col.delete(ids=to_delete)
        print(f"已删除 {len(to_delete)} 块")
    updated = 0
    for batch_ids, batch_metas, batch_docs, batch_embeds in rename_batches:
        col.update(ids=batch_ids, metadatas=batch_metas, documents=batch_docs,
                   embeddings=batch_embeds)
        updated += len(batch_ids)
    print(f"已重命名 {updated} 块")

    # questions collection 同步（同源乱码问题索引）
    try:
        qcol = client.get_collection("questions")
        qres = qcol.get(limit=1000000, include=["metadatas"])
        q_ids, q_metas = qres["ids"], qres["metadatas"]
        q_upd_ids, q_upd_metas = [], []
        for qid, qm in zip(q_ids, q_metas):
            qsrc = qm.get("source", "")
            if not has_hangul(qsrc):
                continue
            action, new_name, _ = classify(qsrc)
            if action in ("rename_main", "rename_alias") and new_name:
                qm["source"] = new_name
                q_upd_ids.append(qid)
                q_upd_metas.append(qm)
            elif action == "delete":
                qcol.delete(ids=[qid])
        if q_upd_ids:
            qcol.update(ids=q_upd_ids, metadatas=q_upd_metas)
        print(f"questions 同步: 重命名 {len(q_upd_ids)}，删除（含乱码主名）")
    except Exception as e:
        print(f"questions 同步跳过: {e}")

    print("\n完成。")


if __name__ == "__main__":
    main()
