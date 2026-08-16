# -*- coding: utf-8 -*-
"""瓷器数据集 ingestor——注释性示例：展示「注册 → 扫描 → 构建 → 加载」完整接入范式

⚠️ 本文件是**注释性示例**:
    - 不自动注册（注册调用在文件底部，默认注释状态）
    - 不重导真实数据——直接运行仅做「扫描 + 构建」演练，不写 Chroma / image_index
    - 真实数据路径 E:/桌面/软创赛/datasets/瓷器/瓷器.xlsx 可能不存在，
      不存在时 scan() 退回内置 stub 数据演示流程

接入新数据源时复制本范式:
    1. 继承 BaseIngestor，实现 scan() / build_chunks()
    2. 底部注册: register("porcelain", PorcelainIngestor)
    3. CLI 调用: python -m services.ingest --source porcelain [--dry-run] [--limit N]
"""
from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

# 兼容两种运行方式: 包内导入（CLI / pytest）与直接运行（python examples/porcelain_ingestor.py）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # src/
    from ingestion.application.ingest_base import BaseIngestor, RawSource
else:
    from ingestion.application.ingest_base import BaseIngestor, RawSource

# 真实数据路径（软创赛本地数据集，可能不存在）
PORCELAIN_EXCEL = Path(r"E:/桌面/软创赛/datasets/瓷器/瓷器.xlsx")

# stub 数据: Excel 缺失时演示用（结构对齐 瓷器.xlsx 的 kiln_name / artifact_name /
# artifact_intro 三列；artifact_intro 为带 "N. 标题：" 小节的鉴定文）
STUB_ROWS = [
    {
        "kiln_name": "宣德",
        "artifact_name": "青花梅瓶",
        "artifact_intro": (
            "1. 釉色：青花色泽浓艳，釉面肥润，呈现典型的宣德苏麻离青特征。\n"
            "2. 胎体：胎质细腻洁白，器壁厚薄均匀，底部露胎处可见火石红。\n"
            "3. 圈足：圈足规整，足墙内敛，底书'大明宣德年制'六字楷书款。"
        ),
    },
    {
        "kiln_name": "龙泉",
        "artifact_name": "青瓷梅子青釉瓶",
        "artifact_intro": (
            "1. 釉层：釉层丰厚如堆脂，梅子青釉色温润含蓄，釉面开片自然。\n"
            "2. 装饰：颈肩部饰弦纹，腹部素面无纹，追求釉色本身的审美。"
        ),
    },
]


def split_sections(text: str) -> list[tuple[str, str]]:
    """鉴定文按 "N. 标题：" 切小节（与 scripts/import_porcelain_chroma.py 同款切法）"""
    pattern = r"(\d+)\.\s*([^\n：]+)[：:]\s*"
    parts = re.split(pattern, text)
    sections = []
    i = 1
    while i < len(parts) - 1:
        title = parts[i + 1].strip()
        content = parts[i + 2].strip() if i + 2 < len(parts) else ""
        if title and content:
            sections.append((title, content))
        i += 3
    return sections


class PorcelainIngestor(BaseIngestor):
    """瓷器数据集接入示例

    source 命名: {窑口}-{器名}（如 宣德-青花梅瓶），符合 P1-C 数据契约；
    每件器物 1 个 RawSource，build_chunks 按鉴定文小节切块（chunk_type=text）。
    若未来接入瓷器图片数据，可在 RawSource.images 登记
    {图片名: 相对路径}，并由 build_chunks 产出 image_path 带 /api/images/ 前缀的
    图片块——load 会自动把映射合并进 image_index.json（见 BaseIngestor._update_image_index）。
    """

    def scan(self) -> list[RawSource]:
        """扫描数据源: 读 瓷器.xlsx（缺失时用 stub 数据），每件器物一个 RawSource"""
        rows = self._load_rows()
        raws = []
        for row in rows:
            kiln = row["kiln_name"]
            name = row["artifact_name"]
            intro = row["artifact_intro"]
            raws.append(
                RawSource(
                    source=f"{kiln}-{name}",
                    text=f"【{kiln}】{name} 鉴定文\n{intro}",
                    path=str(PORCELAIN_EXCEL) if PORCELAIN_EXCEL.exists() else None,
                )
            )
        return raws

    def _load_rows(self) -> list[dict]:
        """读取 Excel（列结构与 import_porcelain_chroma.py 对齐）；缺失时返回 stub"""
        if not PORCELAIN_EXCEL.exists():
            print(f"[porcelain] {PORCELAIN_EXCEL} 不存在，使用内置 stub 数据演示")
            return STUB_ROWS

        import pandas as pd

        df = pd.read_excel(PORCELAIN_EXCEL)
        df.columns = ["kiln_name", "kiln_intro", "source_url",
                      "artifact_name", "artifact_intro", "source_url2"][: len(df.columns)]
        df["kiln_name"] = df["kiln_name"].ffill()
        rows = []
        for _, row in df.iterrows():
            kiln = str(row.get("kiln_name", "")).strip()
            name = str(row.get("artifact_name", "")).strip()
            intro = str(row.get("artifact_intro", "")) if not pd.isna(row.get("artifact_intro")) else ""
            if kiln and name and intro.strip():
                rows.append({"kiln_name": kiln, "artifact_name": name, "artifact_intro": intro})
        return rows

    def build_chunks(self, raw: RawSource) -> list[dict]:
        """原始数据 → 入库块: 按鉴定文小节切块，metadata 带 source/chunk_type"""
        sections = split_sections(raw.text)
        if not sections:
            return [self._make_chunk(raw, "鉴定全文", raw.text)]
        return [self._make_chunk(raw, title, content) for title, content in sections]

    def _make_chunk(self, raw: RawSource, title: str, content: str) -> dict:
        """单块构造——chunk_id/content/metadata（metadata 含 source + chunk_type）"""
        return {
            "chunk_id": str(uuid.uuid4()),
            "content": content.strip(),
            "metadata": {
                "source": raw.source,
                "chunk_type": "text",   # P1-A 契约: 文本块显式标记
                "section": title,
            },
        }


# ⚠️ 接入真实数据前取消注释（含 import）——注册后 CLI 才能 `--source porcelain` 调用：
# from ingestion.application.ingest_service import register
# register("porcelain", PorcelainIngestor)


if __name__ == "__main__":
    # 演示「扫描 → 构建」两步——不调 load，不写任何库（不重导真实数据）
    ing = PorcelainIngestor()
    raws = ing.scan()
    total = 0
    for raw in raws[:5]:
        chunks = ing.build_chunks(raw)
        total += len(chunks)
        print(f"  {raw.source}: {len(chunks)} 块")
    print(f"共扫描 {len(raws)} 个数据源，构建 {total} 块（演示——未入库）")
    print("接入真实数据: 取消底部 register(...) 注释，再用 "
          "python -m services.ingest --source porcelain --dry-run 演练")
