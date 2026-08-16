# 多模态阶段 2/3/4 实施计划（图片映射 + 展示接线 + 河南图片块脚本）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 瓷器/青铜器图片落盘 + image_index.json 映射表 + /api/images 服务 + sources 带图 + 前端展示；河南图注级入库脚本（待用户爬完执行）。

**Architecture:** 纯静态资产处理（无 embedding、不进 Chroma，除阶段 4）。映射表 JSON 驱动前端展示；河南图片块进 documents 集合（chunk_type=image + source `#图` 后缀）。

**Tech Stack:** 同前（Python + FastAPI + Vue3；无 git，Commit 步骤替换为验证命令）。

**Spec:** `docs/superpowers/specs/2026-08-07-multimodal-datasets-design.md`（最终方案）

## Global Constraints

- 注释/文案中文；项目无 git 仓库。
- 后端测试：`cd Backend && uv run pytest tests/ -v`（当前 52 passed）。
- 前端构建门：`cd Frontend && npm run build`。
- 数据集路径：瓷器 `E:/桌面/软创赛/datasets/瓷器/`、青铜器 `E:/桌面/软创赛/datasets/青铜器/complete_DATASET/`；图片目标根 `Backend/src/data/images/`。
- 河南爬虫正在用户侧运行（4-6h）：阶段 4 脚本只写不跑；`--source henan` 分支执行前检查 `src/data/henan_images.json` 是否存在。
- 不触碰运行中的爬虫输出目录（src/data/images/henan/ 只读）。

---

### Task M1: 静态图片落盘 + 映射表脚本

**Files:**
- Create: `Backend/scripts/import_dataset_images.py`
- Test: `Backend/tests/test_image_index.py`

**Interfaces:**
- Produces:
  ```python
  # scripts/import_dataset_images.py --source porcelain|bronze|henan
  # 输出 src/data/image_index.json:
  #   { "bronze-叩鼎": {"primary": "images/bronze/101014.png", "images": [...]}, ... }
  #   source 键与 Chroma 文本块 source 一致（青铜: 青铜-{器名}；瓷器: {目录名}；河南: 河南博物院-{文物名}）
  def build_image_index(sources: list[str]) -> dict          # 合并多源映射
  def scan_porcelain(base: Path) -> dict                      # 目录名 → 图片列表
  def scan_bronze(png_dir: Path, excel_path: Path) -> tuple[dict, int]  # (映射, 未匹配数)
  def scan_henan(manifest_json: Path) -> dict                 # 文物名 → 图片列表
  ```

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_image_index.py`:

```python
"""image_index 扫描与映射测试（纯文件系统，不触网）"""
from pathlib import Path

from scripts.import_dataset_images import scan_porcelain, scan_bronze


def test_scan_porcelain(tmp_path):
    d1 = tmp_path / "宣德-青花梅瓶"
    d2 = tmp_path / "元代-釉里红玉壶春瓶"
    d1.mkdir(); d2.mkdir()
    (d1 / "a.png").write_bytes(b"1")
    (d1 / "b.png").write_bytes(b"2")
    (d2 / "c.png").write_bytes(b"3")

    mapping = scan_porcelain(tmp_path)
    # 映射表存相对 IMAGES_ROOT 的路径（不含 images/ 前缀，服务层拼 /api/images/）
    assert mapping["宣德-青花梅瓶"] == ["porcelain/宣德-青花梅瓶/a.png",
                                        "porcelain/宣德-青花梅瓶/b.png"]
    assert mapping["元代-釉里红玉壶春瓶"] == ["porcelain/元代-釉里红玉壶春瓶/c.png"]


def test_scan_bronze_matches_excel(tmp_path):
    import pandas as pd

    png = tmp_path / "png"; png.mkdir()
    (png / "101001.png").write_bytes(b"1")
    (png / "999999.png").write_bytes(b"2")  # Excel 无此行 → 未匹配
    excel = tmp_path / "train.xlsx"
    pd.DataFrame({
        "编号": ["101001", "銘三_0034"],
        "器名": ["素面弦纹鼎", "叩鼎"],
        "时代": [1, 1],
        "器形": [17, 18],
        "现藏": ["翁牛特旗博物馆", "-"],
        "出土时地": ["内蒙古赤峰", "-"],
    }).to_excel(excel, index=False)

    mapping, missed = scan_bronze(png, excel)
    assert mapping["青铜-素面弦纹鼎"] == ["bronze/101001.png"]
    assert missed == 1  # 999999 无对应行
    assert "999999" not in str(mapping)


def test_build_image_index_merges(tmp_path):
    from scripts.import_dataset_images import build_image_index

    merged = build_image_index([
        {"宣德-青花梅瓶": ["porcelain/宣德-青花梅瓶/a.png"]},
        {"青铜-叩鼎": ["bronze/101014.png"]},
    ])
    assert merged["宣德-青花梅瓶"]["primary"] == "porcelain/宣德-青花梅瓶/a.png"
    assert merged["青铜-叩鼎"]["primary"] == "bronze/101014.png"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_image_index.py -v`
Expected: FAIL（ModuleNotFoundError: scripts.import_dataset_images）。

- [ ] **Step 3: 实现脚本**

Create `Backend/scripts/import_dataset_images.py`:

```python
# -*- coding: utf-8 -*-
"""数据集图片落盘 + 映射表生成（多模态阶段 2）

用法:
  uv run python scripts/import_dataset_images.py --source porcelain
  uv run python scripts/import_dataset_images.py --source bronze
  uv run python scripts/import_dataset_images.py --source henan   # 等爬虫跑完

行为:
  - 瓷器: YMbwp-Dataset 70 目录全部图片复制到 src/data/images/porcelain/{目录名}/
  - 青铜器: ori_png 3697 张复制到 src/data/images/bronze/{编号}.png
  - 河南: 读 henan_images.json（爬虫产物），不复制（已在目标位置），仅登记
  - 输出 src/data/image_index.json: {source: {primary, images[]}}（幂等合并）

映射键与 Chroma 文本块 source 一致: 青铜-{器名} / {窑口-器名} / 河南博物院-{文物名}
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

# 允许 scripts/ 下直接运行
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

DATA_DIR = Path(__file__).parent.parent / "src" / "data"
IMAGES_ROOT = DATA_DIR / "images"
INDEX_OUT = DATA_DIR / "image_index.json"

PORCELAIN_BASE = Path(r"E:/桌面/软创赛/datasets/瓷器/YMbwp-Dataset")
BRONZE_BASE = Path(r"E:/桌面/软创赛/datasets/青铜器/complete_DATASET/ori_png")
BRONZE_EXCEL = Path(r"E:/桌面/软创赛/datasets/青铜器/complete_DATASET/train.xlsx")
HENAN_MANIFEST = DATA_DIR / "henan_images.json"


def _rel(p: Path) -> str:
    """相对 IMAGES_ROOT 的正斜杠路径（URL 友好）"""
    return p.relative_to(IMAGES_ROOT).as_posix()


def scan_porcelain(base: Path) -> dict:
    """瓷器: 目录名(=source) → 全部图片（复制后登记）"""
    mapping: dict[str, list[str]] = {}
    if not base.exists():
        return mapping
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        src_name = d.name  # 如 宣德-青花梅瓶（与 Chroma source 一致）
        target = IMAGES_ROOT / "porcelain" / src_name
        target.mkdir(parents=True, exist_ok=True)
        files = []
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                dst = target / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
                files.append(_rel(dst))
        if files:
            mapping[src_name] = files
    return mapping


def scan_bronze(png_dir: Path, excel_path: Path) -> tuple[dict, int]:
    """青铜器: 图片编号 → Excel 编号列 → 器名 → source=青铜-{器名}"""
    import pandas as pd

    mapping: dict[str, list[str]] = {}
    if not png_dir.exists() or not excel_path.exists():
        return mapping, 0
    df = pd.read_excel(excel_path)
    id2name = {}
    for _, row in df.iterrows():
        cid = str(row.get("编号", "")).strip()
        name = str(row.get("器名", "")).strip()
        if cid and name and name != "nan":
            id2name[cid] = name

    target = IMAGES_ROOT / "bronze"
    target.mkdir(parents=True, exist_ok=True)
    missed = 0
    for f in sorted(png_dir.glob("*.png")):
        cid = f.stem
        name = id2name.get(cid)
        if not name:
            missed += 1
            continue
        dst = target / f.name
        if not dst.exists():
            shutil.copy2(f, dst)
        mapping.setdefault(f"青铜-{name}", []).append(_rel(dst))
    return mapping, missed


def scan_henan(manifest: Path) -> dict:
    """河南: 读爬虫产物 henan_images.json → source=河南博物院-{文物名}"""
    mapping: dict[str, list[str]] = {}
    if not manifest.exists():
        return mapping
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for key, entry in data.items():
        name = entry.get("name", key)
        files = [f"images/henan/{key}/{img['file']}" for img in entry.get("images", [])]
        if files:
            mapping[f"河南博物院-{name}"] = files
    return mapping


def build_image_index(partials: list[dict]) -> dict:
    """合并多源映射为 {source: {primary, images[]}}"""
    merged: dict[str, dict] = {}
    for partial in partials:
        for source, files in partial.items():
            if not files:
                continue
            merged[source] = {"primary": files[0], "images": files}
    return merged


def main():
    parser = argparse.ArgumentParser(description="数据集图片落盘与映射表")
    parser.add_argument("--source", choices=["porcelain", "bronze", "henan"], required=True)
    args = parser.parse_args()

    IMAGES_ROOT.mkdir(parents=True, exist_ok=True)
    partials: list[dict] = []

    if args.source == "porcelain":
        partials.append(scan_porcelain(PORCELAIN_BASE))
        print(f"瓷器: 扫描 {PORCELAIN_BASE}")
    elif args.source == "bronze":
        mapping, missed = scan_bronze(BRONZE_BASE, BRONZE_EXCEL)
        partials.append(mapping)
        print(f"青铜器: 未匹配编号 {missed} 个（无 Excel 对应行）")
    elif args.source == "henan":
        partials.append(scan_henan(HENAN_MANIFEST))
        print(f"河南: 读 {HENAN_MANIFEST}")

    # 与既有映射合并（幂等，可重跑）
    existing: dict = {}
    if INDEX_OUT.exists():
        existing = json.loads(INDEX_OUT.read_text(encoding="utf-8"))
    for source, entry in build_image_index(partials).items():
        existing[source] = entry

    INDEX_OUT.write_text(
        json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"映射表: {INDEX_OUT}（{len(existing)} 个 source）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_image_index.py -v`
Expected: 3 passed。

- [ ] **Step 5: 实际执行（瓷器 + 青铜器）**

Run: `cd E:/projects/DocuMind/Backend && uv run python scripts/import_dataset_images.py --source porcelain && uv run python scripts/import_dataset_images.py --source bronze`
Expected: 复制完成；`image_index.json` 存在，`len` 与瓷器 70 + 青铜器匹配数一致；抽查：
`uv run python -c "import json; d=json.load(open('src/data/image_index.json',encoding='utf-8')); print(len(d)); print(list(d.items())[:2])"`

- [ ] **Step 6: 不触碰河南目录检查**

Run: `ls src/data/images/ | grep -c henan`（爬虫正在写）——只读确认，不复制不删除。

---

### Task M2: 后端接线（/api/images + image_index 服务 + sources 带图）

**Files:**
- Create: `Backend/src/services/image_index.py`
- Modify: `Backend/src/main.py`、`Backend/src/services/agent/orchestrator.py`
- Test: `Backend/tests/test_image_index_service.py`

**Interfaces:**
- Consumes: `src/data/image_index.json`（M1 产物）。
- Produces:
  ```python
  # services/image_index.py
  def load_image_index() -> dict                              # 懒加载 + 缓存
  def get_images_for_source(source: str) -> list[str]         # [带 /api/images/ 前缀的 URL]，无 → []
  # main.py: app.mount("/api/images", StaticFiles(directory="src/data/images"))
  # orchestrator quick_answer sources items 增加 "images": get_images_for_source(source)
  ```

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_image_index_service.py`:

```python
"""image_index 服务测试"""
import json

from services.image_index import load_image_index, get_images_for_source


def test_load_and_query(tmp_path, monkeypatch):
    idx = {
        "青铜-叩鼎": {"primary": "images/bronze/101014.png",
                      "images": ["images/bronze/101014.png"]},
        "宣德-青花梅瓶": {"primary": "images/porcelain/宣德-青花梅瓶/a.png",
                        "images": ["images/porcelain/宣德-青花梅瓶/a.png",
                                   "images/porcelain/宣德-青花梅瓶/b.png"]},
    }
    (tmp_path / "image_index.json").write_text(
        json.dumps(idx, ensure_ascii=False), encoding="utf-8"
    )
    from services import image_index as mod
    monkeypatch.setattr(mod, "INDEX_PATH", tmp_path / "image_index.json")
    monkeypatch.setattr(mod, "_cache", None)

    urls = get_images_for_source("青铜-叩鼎")
    # 映射表存相对路径（bronze/101014.png），服务层拼 /api/images/ 前缀，不重复 images/
    assert urls == ["/api/images/bronze/101014.png"]
    assert get_images_for_source("不存在的source") == []
```

（注意：image_path 存的是 `images/bronze/...`（相对 IMAGES_ROOT 的路径），静态根是 `src/data/images`——URL 应为 `/api/images/bronze/...`，**不要**重复 `images/` 前缀。实现时统一：`_rel` 存相对根路径，服务层拼 `/api/images/` 前缀。测试断言 `startswith("/api/images/")` 且不含 `images/images`。）

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_image_index_service.py -v`
Expected: FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 实现**

1) Create `Backend/src/services/image_index.py`:

```python
"""图片映射表服务——source → 图片 URL（多模态阶段 3）

数据源: src/data/image_index.json（scripts/import_dataset_images.py 生成）
语义: 检索命中文本块时，按 source 查映射表，把该器物的图片随 sources 返回，
     前端证据链展示。图片本身不进向量库。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

INDEX_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "image_index.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    """加载映射表（进程内缓存，重新生成后重启服务生效）"""
    try:
        if INDEX_PATH.exists():
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"image_index 加载失败: {e}")
    return {}


def load_image_index() -> dict:
    return _load()


def get_images_for_source(source: str) -> list[str]:
    """source → 带 /api/images/ 前缀的图片 URL 列表；无图返回 []"""
    entry = _load().get(source)
    if not entry:
        return []
    files = entry.get("images") or []
    return [f"/api/images/{f}" for f in files if f]
```

2) Modify `Backend/src/main.py`，在 `/api/uploads` 挂载之后加：

```python
# ── 数据集图片静态服务（多模态：映射表驱动展示，与 uploads 同根 src/data）──
IMAGES_DIR = Path(settings.upload_dir).parent / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")
```

（`Path(settings.upload_dir).parent` = `src/data`，与 image_index.json 的相对路径根一致；`Path` 顶部已 import。）

3) Modify `Backend/src/services/agent/orchestrator.py`：

   a) 顶部 import：`from services.image_index import get_images_for_source`
   b) quick_answer 的 sources items 映射加字段（在 `"image_url": d.get("metadata", {}).get("image_path"),` 之后）：

```python
                            "images": get_images_for_source(d.get("source", "")),
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_image_index_service.py -v`
Expected: 1 passed（实现按测试修正 URL 前缀逻辑）。

- [ ] **Step 5: 全量回归**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/ -v`
Expected: 52 + 3（M1）+ 1 = 56 passed。

---

### Task M3: 前端展示接线

**Files:**
- Modify: `Frontend/src/types/api.ts`、`Frontend/src/components/EvidencePanel.vue`

**Interfaces:**
- Consumes: sources items 的 `images: string[]`（带 /api/images/ 前缀）+ 既有 `image_url`。
- Produces: `SourceItem.images?: string[]`；EvidencePanel 来源项渲染多图缩略图（与 image_url 单图共存：images 优先）。

- [ ] **Step 1: 类型与展示**

1) Modify `Frontend/src/types/api.ts`，`SourceItem` 加：

```ts
  /** 关联图片（映射表驱动，多图；带 /api/images/ 前缀） */
  images?: string[]
```

2) Modify `Frontend/src/components/EvidencePanel.vue`：

   a) Template：把现有单图 `<img v-if="s.image_url" ...>` 替换为多图块：

```vue
          <div v-if="s.images && s.images.length" class="src-images">
            <img
              v-for="(img, k) in s.images.slice(0, 3)"
              :key="k"
              :src="imageAbsUrl(img)"
              class="src-image"
              loading="lazy"
              @click="openImage(img)"
            />
          </div>
          <img
            v-else-if="s.image_url"
            :src="imageAbsUrl(s.image_url)"
            class="src-image"
            loading="lazy"
            @click="openImage(s.image_url)"
          />
```

   b) Style 增加：

```less
.src-images {
  display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px;
  .src-image { max-height: 90px; }
}
```

（`.src-image` 现有样式改 `max-height: 120px` → 多图时缩小为 90px？保留 120px 单图，多图容器内 90px——用嵌套覆盖。）

   c) `imageAbsUrl` 已兼容 `/api/images/...`（`/` 开头直拼）——零改动。

- [ ] **Step 2: 构建验证**

Run: `cd E:/projects/DocuMind/Frontend && npm run build`
Expected: vue-tsc 零错误 + `✓ built`。

- [ ] **Step 3: 手动冒烟（浏览器，待用户）**

问"叩鼎" → 证据链叩鼎条目旁显示青铜器图片（来自映射表）；问"宣德青花梅瓶" → 显示瓷器图；无图来源不显示图片区。

---

### Task M4: 河南图注级入库脚本（只写不跑，等爬虫完成）

**Files:**
- Create: `Backend/scripts/import_henan_image_chunks.py`
- Test: `Backend/tests/test_henan_image_chunks.py`

**Interfaces:**
- Consumes: `src/data/henan_images.json`（爬虫产物）+ `src/data/henan_museum.json`（文本）。
- Produces: 图片块文档列表函数 `build_image_chunks(manifest: dict, museum: dict) -> list[dict]`；脚本将图片块写入 `container.vector`（documents 集合）并 `mark_bm25_dirty`。

- [ ] **Step 1: 写失败测试**

Create `Backend/tests/test_henan_image_chunks.py`:

```python
"""河南图注级图片块构造测试（纯函数，不触网不入库）"""
from scripts.import_henan_image_chunks import build_image_chunks


def test_build_image_chunks_with_caption():
    manifest = {
        "妇好墓玉龙": {
            "name": "妇好墓玉龙",
            "images": [
                {"file": "01.jpg", "figure_no": "1",
                 "caption": "图1  甲骨文所见“龙”字写法", "section": "", "context": ""},
                {"file": "02.jpg", "figure_no": "", "caption": "", "section": "", "context": ""},
            ],
        }
    }
    chunks = build_image_chunks(manifest, {})
    assert len(chunks) == 2
    c = chunks[0]
    assert "【图片·图1】" in c["content"]
    assert "甲骨文" in c["content"]
    assert c["metadata"]["source"] == "河南博物院-妇好墓玉龙#图"
    assert c["metadata"]["chunk_type"] == "image"
    assert c["metadata"]["image_path"] == "/api/images/henan/妇好墓玉龙/01.jpg"
    assert c["metadata"]["figure_no"] == "1"
    # 无图注的第二张：占位内容含文件名
    assert "02.jpg" in chunks[1]["content"]


def test_build_image_chunks_skips_empty():
    assert build_image_chunks({}, {}) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_henan_image_chunks.py -v`
Expected: FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 实现**

Create `Backend/scripts/import_henan_image_chunks.py`:

```python
# -*- coding: utf-8 -*-
"""河南图注级图片块入库（多模态阶段 4）——等 crawl_henan_images.py 跑完再执行

用法: uv run python scripts/import_henan_image_chunks.py

行为:
  - 读 henan_images.json（爬虫产物: file/figure_no/caption/section）
  - 每张图 1 个图片块进 documents 集合:
      content = 【图片·图{N}】{caption}（无图注 → 【图片】{文物名} {file}）
      metadata = {source: "河南博物院-{文物名}#图", chunk_type: "image",
                  image_path: "/api/images/henan/{文物名}/{file}", figure_no}
  - source 的 #图 后缀: 与文本块隔离来源多样性上限
  - 幂等: 已存在的 image_path 跳过（按 metadata 查重）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

DATA_DIR = Path(__file__).parent.parent / "src" / "data"
MANIFEST = DATA_DIR / "henan_images.json"


def build_image_chunks(manifest: dict, museum: dict) -> list[dict]:
    """manifest → 图片块文档列表（纯函数，可测试）"""
    import uuid

    chunks = []
    for key, entry in manifest.items():
        name = entry.get("name", key)
        for img in entry.get("images", []):
            fname = img.get("file", "")
            if not fname:
                continue
            fig = img.get("figure_no", "")
            caption = (img.get("caption") or "").strip()
            if fig:
                content = f"【图片·图{fig}】{caption}" if caption else f"【图片·图{fig}】{name}"
            else:
                content = f"【图片】{name} {fname}"
            chunks.append({
                "chunk_id": str(uuid.uuid4()),
                "content": content,
                "metadata": {
                    "source": f"河南博物院-{name}#图",
                    "chunk_type": "image",
                    "image_path": f"/api/images/henan/{key}/{fname}",
                    "figure_no": fig,
                    "section": img.get("section", ""),
                },
            })
    return chunks


def main():
    if not MANIFEST.exists():
        print("henan_images.json 不存在——先跑 crawl_henan_images.py")
        return
    from core.di import container

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    museum_path = DATA_DIR / "henan_museum.json"
    museum = json.loads(museum_path.read_text(encoding="utf-8")) if museum_path.exists() else {}
    chunks = build_image_chunks(manifest, museum)
    print(f"构造图片块 {len(chunks)} 个")

    # 幂等：跳过已入库（按 image_path 查重）
    existing = set()
    try:
        docs = container.vector.get_all_documents()
        for d in docs:
            ip = d.get("metadata", {}).get("image_path")
            if ip and ip.startswith("/api/images/henan/"):
                existing.add(ip)
    except Exception as e:
        print(f"查重失败（继续全量）: {e}")
    fresh = [c for c in chunks if c["metadata"]["image_path"] not in existing]
    print(f"新增 {len(fresh)} 个（已存在 {len(chunks) - len(fresh)} 个）")

    if fresh:
        container.vector.add_documents(fresh)
        container.mark_bm25_dirty()
    print("完成")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/test_henan_image_chunks.py -v`
Expected: 2 passed。

- [ ] **Step 5: 全量回归**

Run: `cd E:/projects/DocuMind/Backend && uv run pytest tests/ -v`
Expected: 58 passed。**不执行 main()**（等用户爬完，执行前确认 henan_images.json 存在且条目完整）。

---

## 回归清单

| 任务 | 回归命令 |
|------|---------|
| M1-M4 | `cd Backend && uv run pytest tests/ -v`（最终 58 passed） |
| M3 | `cd Frontend && npm run build` |
| 用户侧 | 爬完后跑 `uv run python scripts/import_henan_image_chunks.py` 或告知我执行 |

## 交付顺序

M1（含实际执行瓷器/青铜器）→ M2 → M3 → M4（只写不跑）
