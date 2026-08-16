# -*- coding: utf-8 -*-
"""Import crawled Henan Museum data into Chroma.

Reads src/data/henan_museum.json (crawl_henan_museum.py output), chunks each
artifact's prose into ~600-char sections (section titles like "一、..." are
chunk boundaries), and adds them to the documents collection with metadata
aligned to the existing porcelain format (kiln/artifact/section/source) so
tree-pruning and Q-to-Q retrieval work unchanged.

Idempotent: deletes previous "河南博物院-" sources, then re-imports.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402

DATA_FILE = Path(__file__).parent.parent / "src" / "data" / "henan_museum.json"
CHUNK_SIZE = 600
MIN_CHUNK = 30


def load_data() -> dict:
    if not DATA_FILE.exists():
        print(f"[ERR] {DATA_FILE} not found - run crawl_henan_museum.py first")
        sys.exit(1)
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def split_sections(text: str, max_chars: int = CHUNK_SIZE) -> list[str]:
    """Split prose into chunks at sentence boundaries, cap at max_chars."""
    sentences = re.split(r"(?<=[。！？；])", text)
    chunks, buf = [], ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(buf) + len(s) > max_chars and buf:
            chunks.append(buf)
            buf = s
        else:
            buf += s
    if buf and len(buf) >= MIN_CHUNK:
        chunks.append(buf)
    return chunks


# 列表页友链混入的机构条目（非文物），整条丢弃
INSTITUTION_WORDS = ["博物馆", "博物院", "研究院", "官网", "文化中心", "文物局"]
# 正文尾部无关区块（开放时间/地址/参观须知）截断标记
TAIL_MARKERS = ["开放时间", "参观须知", "地址：", "地址:", "公交", "地铁", "预约方式"]


def _is_institution(name: str) -> bool:
    return any(w in name for w in INSTITUTION_WORDS)


def _truncate_tail(sections: list[str]) -> list[str]:
    """Drop trailing boilerplate (opening hours, address, etc)."""
    for i, s in enumerate(sections):
        if any(m in s for m in TAIL_MARKERS):
            return sections[:i]
    return sections


def build_docs(data: dict) -> list[dict]:
    """One artifact -> multiple chunks with section-aware metadata."""
    documents = []
    skipped = 0
    for cid, art in data.items():
        name = art.get("name", "")
        sections = _truncate_tail(art.get("sections", []))
        full = art.get("full_text", "")
        if not name or not full.strip():
            continue
        if _is_institution(name):
            skipped += 1
            continue

        # Section titles act as chunk boundaries when present
        title_idxs = [
            i for i, s in enumerate(sections)
            if re.match(r"^[一二三四五六七八九十百]+、", s.strip())
        ]
        if title_idxs:
            groups = []
            for idx, t in enumerate(title_idxs):
                end = title_idxs[idx + 1] if idx + 1 < len(title_idxs) else len(sections)
                groups.append((sections[t], " ".join(sections[t + 1 : end])))
            for title, body in groups:
                for chunk in split_sections(body):
                    if len(chunk) < MIN_CHUNK:
                        continue
                    documents.append({
                        "content": f"{name} · {title}\n{chunk}",
                        "metadata": {
                            "source": f"河南博物院-{name}",
                            "kiln": "河南博物院",
                            "artifact": name,
                            "section": title,
                        },
                    })
        else:
            # No section titles: whole text as one logical chunk split by size
            intro = sections[0] if sections else ""
            for chunk in split_sections(full):
                if len(chunk) < MIN_CHUNK:
                    continue
                documents.append({
                    "content": f"{name}\n{chunk}",
                    "metadata": {
                        "source": f"河南博物院-{name}",
                        "kiln": "河南博物院",
                        "artifact": name,
                        "section": "鉴赏全文" if chunk == intro else "赏析",
                    },
                })
    if skipped:
        print(f"Skipped institution entries: {skipped}")
    return documents


def main():
    print("=== Import Henan Museum to Chroma ===\n")

    data = load_data()
    print(f"Artifacts: {len(data)}")

    docs = build_docs(data)
    print(f"Chunks: {len(docs)}")

    # Clear previous henan sources (idempotent re-import)
    vs = container.vector
    old = [s for s in vs.list_sources() if s.startswith("河南博物院-")]
    for src in old:
        vs.delete(src)
    print(f"Cleared: {len(old)} old henan sources")

    batch = 100
    for i in range(0, len(docs), batch):
        vs.add_documents(docs[i : i + batch])
        print(f"  {min(i + batch, len(docs))}/{len(docs)}")

    print(f"\nDone! Total chunks now: {vs.count()}")

    # Spot-check retrieval
    print("\n=== Retrieval Test ===")
    for query in ["汝窑天青釉盏托的形制", "河南博物院藏青铜器", "莲鹤方壶"]:
        results = vs.retrieve(query, top_k=2)
        print(f"\nQ: {query}")
        for r in results:
            m = r.get("metadata", {})
            print(f"  [{r['score']:.3f}] {m.get('artifact', '?')} / {m.get('section', '?')}")


if __name__ == "__main__":
    main()
