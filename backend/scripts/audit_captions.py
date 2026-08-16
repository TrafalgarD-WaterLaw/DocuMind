# -*- coding: utf-8 -*-
"""VLM 图注描述质检报告——抽样 30 张(河南 20 + 数据集 5 + 上传文档 5)

产出 HTML 报告(图 + VLM 描述 + 来源并排),供人工评分:
- 河南:补描述(qwen3.7-plus)的图注级图片块
- 数据集:bronze/porcelain 映射表图片(图片本体,非描述块)
- 上传文档:uploads 下的解析图片(如有)

用法: python scripts/audit_captions.py [--out path] [--seed N]
"""
import argparse
import base64
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.stdout.reconfigure(encoding="utf-8")

HENAN_N = 20
DATASET_N = 5
UPLOAD_N = 5


def _img_b64(path: Path) -> str:
    data = path.read_bytes()
    ext = path.suffix.lstrip(".").lower() or "png"
    return f"data:image/{ext};base64," + base64.b64encode(data).decode("ascii")


def _sample_henan(rng: random.Random) -> list[dict]:
    """河南图片块:抽 20 张(补描述后有【图片·图N】描述的)

    注意:Chroma $contains 只对数组字段生效,字符串子串匹配在 Python 侧过滤。
    """
    import chromadb

    from core.config import settings

    col = chromadb.PersistentClient(path=settings.chroma_persist_dir).get_collection("documents")
    res = col.get(
        where={"chunk_type": "image"}, include=["documents", "metadatas"], limit=10000,
    )
    rows = []
    for cid, content, meta in zip(res["ids"], res["documents"], res["metadatas"]):
        if not ((meta or {}).get("source", "") or "").startswith("河南博物院"):
            continue
        if "【图片·图" not in content:
            continue
        src = (meta or {}).get("source", "")
        img_rel = (meta or {}).get("image_path", "").removeprefix("/api/images/")
        rows.append((cid, src, content, img_rel))
    for cid, content, meta in zip(res["ids"], res["documents"], res["metadatas"]):
        if "【图片·图" not in content:
            continue
        src = (meta or {}).get("source", "")
        img_rel = (meta or {}).get("image_path", "").removeprefix("/api/images/")
        rows.append((cid, src, content, img_rel))
    picks = rng.sample(rows, min(HENAN_N, len(rows)))
    out = []
    for cid, src, content, img_rel in picks:
        img = Path("src/data/images") / img_rel
        out.append({
            "domain": "河南(补描述)", "source": src, "content": content,
            "img": _img_b64(img) if img.exists() else None,
        })
    return out


def _sample_dataset(rng: random.Random) -> list[dict]:
    """数据集图片(bronze/porcelain):抽 5 张,附其图注块描述(如有)"""
    import json

    index = json.loads(Path("src/data/image_index.json").read_text(encoding="utf-8"))
    rows = []
    for src, entry in index.items():
        for rel in (entry.get("images") or [])[:3]:
            img = Path("src/data/images") / rel
            if img.exists():
                rows.append((src, rel))
    picks = rng.sample(rows, min(DATASET_N, len(rows)))
    out = []
    for src, rel in picks:
        img = Path("src/data/images") / rel
        out.append({
            "domain": "数据集", "source": src, "content": "(数据集图片无描述块,评估图片本身)",
            "img": _img_b64(img),
        })
    return out


def _sample_uploads(rng: random.Random) -> list[dict]:
    """上传文档图片块:抽 5 张(如有)"""
    import chromadb

    from core.config import settings

    col = chromadb.PersistentClient(path=settings.chroma_persist_dir).get_collection("documents")
    # Chroma $contains 只对数组生效,字符串子串匹配在 Python 侧过滤
    res = col.get(
        where={"chunk_type": "image"}, include=["documents", "metadatas"], limit=5000,
    )
    rows = []
    for cid, content, meta in zip(res["ids"], res["documents"], res["metadatas"]):
        src = (meta or {}).get("source", "")
        if not src.endswith(".pdf#图"):
            continue
        img_rel = (meta or {}).get("image_path", "").removeprefix("/api/uploads/")
        rows.append((src, content, img_rel))
    picks = rng.sample(rows, min(UPLOAD_N, len(rows)))
    out = []
    for src, content, img_rel in picks:
        img = Path("src/data/uploads") / img_rel
        out.append({
            "domain": "上传文档", "source": src, "content": content,
            "img": _img_b64(img) if img.exists() else None,
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="eval/reports/caption_audit.html")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    items = _sample_henan(rng) + _sample_dataset(rng) + _sample_uploads(rng)

    cards = []
    for i, it in enumerate(items, 1):
        img_html = (
            f'<img src="{it["img"]}" style="max-width:200px;max-height:160px;object-fit:contain;background:#f5f0e8;border:1px solid #c9a96e;border-radius:6px;"/>'
            if it["img"] else '<span style="color:#c41e3a">图片缺失</span>'
        )
        cards.append(f"""
      <div class="card">
        <div class="head"><span class="idx">#{i}</span><span class="dom">{it['domain']}</span><span class="src">{it['source'][:40]}</span></div>
        <div class="body">{img_html}<div class="desc">{it['content']}</div></div>
        <div class="grade">□ 准确 &nbsp; □ 部分错 &nbsp; □ 错误 &nbsp; □ 无法辨认</div>
      </div>""")

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>VLM 描述质检报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; background: #f5f0e8; margin: 20px; }}
h1 {{ font-family: 'STSong', serif; color: #8b4513; }}
.note {{ color: #8b7355; font-size: 13px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }}
.card {{ background: #fdfaf3; border: 1px solid #c9a96e; border-radius: 8px; padding: 10px; }}
.head {{ display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }}
.idx {{ color: #c41e3a; font-weight: 800; }}
.dom {{ font-size: 11px; background: rgba(201,169,110,0.25); color: #8b4513; padding: 1px 8px; border-radius: 9px; }}
.src {{ font-size: 11px; color: #8b7355; overflow: hidden; text-overflow: ellipsis; }}
.body {{ display: flex; gap: 10px; align-items: flex-start; }}
.desc {{ font-size: 12px; color: #2c2c2c; line-height: 1.7; flex: 1; }}
.grade {{ margin-top: 8px; font-size: 12px; color: #8b7355; }}
</style></head><body>
<h1>VLM 图注描述质检报告</h1>
<p class="note">抽样 {len(items)} 张(河南补描述 {HENAN_N} + 数据集 {DATASET_N} + 上传文档 {UPLOAD_N}),seed={args.seed}。
评分维度:准确(描述与图片一致) / 部分错(主体对,细节错) / 错误(主体错) / 无法辨认(合法空输出)。</p>
<div class="grid">{''.join(cards)}
</div></body></html>"""

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"报告已生成: {out} ({len(items)} 张)")


if __name__ == "__main__":
    main()
