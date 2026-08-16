# 多模态数据入库设计（最终方案）

日期：2026-08-07（v2，多轮讨论后定稿）
状态：已确认——河南走图注级图片块；瓷器/青铜器走文本块+图片映射表；图片不进 Chroma（进的是文本）

## 一、架构决策（定稿）

```
图片 = 磁盘文件 + 静态 URL（永不进 Chroma）
Chroma 只存文本：文本块（正文）+ 图片块（描述图片的一行文本，仅河南）
关联机制（双轨）:
  ① 文本块 + 映射表 image_index.json: { source → [图片路径...] }（瓷器/青铜器）
  ② 图片块本身可检索，命中后按 metadata.image_path 取图（河南，图注级）
前端证据链: 来源项显示 映射表代表图（images 字段）+ 图片块精确图（image_url 字段）
```

**明确不做**：VLM 描述（接口已就绪等 DashScope key）；青铜器 XML 标注；河南推荐链扩展；图片去重筛选。

## 二、数据源与方案

| 数据源 | 文本（已有） | 图片 | 方案 |
|---|---|---|---|
| 河南博物院 | 283 条品鉴文章（全量多栏目文本） | 未爬（实测 27 张/页，图注配对率 89%） | **图注级图片块**：爬虫按 DOM 顺序存 block 流+图注 → 每图 1 个图片块入 Chroma |
| 瓷器 | 瓷器.xlsx（窑种类/特征/陶瓷种类/物品特征） | 1956 张 PNG（YMbwp 70 目录，目录名=`{窑口}-{器名}`） | **文本块+映射表**：图片落盘 `images/porcelain/{目录名}/`，source=目录名 |
| 青铜器 | train.xlsx（编号/器名/时代/器形/现藏/出土时地） | 3697 张 PNG（ori_png `{编号}.png`） | **文本块+映射表**：图片落盘 `images/bronze/{编号}.png`，source=`青铜-{器名}`（编号→Excel 器名） |

## 三、阶段任务

### 阶段 1：河南配图爬取（用户手动跑，已开始）
- `scripts/crawl_henan_images.py`（已交付）：283 条 → DOM 顺序 block 流 → 图片下载 `images/henan/{文物名}/NN.jpg` → `henan_images.json`（file/figure_no/caption/section）
- 断点续爬、礼貌爬取、增量落盘。~7600 张 / 4-6h。

### 阶段 2：瓷器/青铜器图片落盘 + 映射表（本计划范围）
- `scripts/import_dataset_images.py`（`--source porcelain|bronze`）：
  - 瓷器：遍历 YMbwp 目录 → 复制全部图到 `images/porcelain/{目录名}/`（幂等）→ 映射 source=目录名，primary=排序第一张
  - 青铜器：遍历 ori_png → 复制到 `images/bronze/{编号}.png` → Excel 编号→器名 → source=`青铜-{器名}`（查不到的跳过并统计）
  - 输出 `src/data/image_index.json`：`{source: {"primary": "相对路径", "images": [全部]}}`
- 河南：`--source henan` 读 henan_images.json 补进同一映射表（source=`河南博物院-{文物名}`）

### 阶段 3：检索展示接线（本计划范围）
- `main.py` 挂载 `/api/images` → `src/data/images`
- `src/services/image_index.py`：加载 image_index.json + `get_images_for_source(source) -> dict`（带 /api/images/ 前缀）
- orchestrator quick_answer 的 sources items 加 `images: [url...]`（按 source 查映射表；无图返回 []）
- 前端：`SourceItem.images?: string[]`；EvidencePanel 来源项渲染多图缩略图（与已有 image_url 单图共存，优先 images 列表）

### 阶段 4：河南图注级入库（爬完后执行，本计划含脚本）
- `import_dataset_images.py --source henan-chroma`：读 henan_images.json + henan_museum.json →
  - 图片块 content = `【图片·图{N}】{caption}`（caption 含图号与标题；语境段落回填留待增强）
  - metadata = {source: `河南博物院-{文物名}#图`, chunk_type: "image", image_path: `/api/images/henan/{文物名}/{file}`, figure_no}
  - 入 documents 集合 → mark_bm25_dirty
- `#图` 后缀：来源多样性（max_per_source=2）与文本块隔离

## 四、验收标准

1. 阶段 2 跑完：瓷器 70 目录 / 1956 图落盘、青铜器 ~3697 图落盘；image_index.json 覆盖 ~2300 source
2. 阶段 3：`/api/images/bronze/101001.png` 浏览器可访问；问"叩鼎"→ 证据链叩鼎条目显示青铜器图；问"宣德青花梅瓶"→ 显示瓷器图
3. 阶段 4（爬完后）：问"甲骨文的龙字怎么写"→ 命中图1 图片块 → 证据链显示对应图；普通查询图片块不挤占文本块
4. 全量 pytest 不回归（52 passed）；前端 build 通过

## 五、实施顺序

阶段 2 → 阶段 3（可先做）→ 阶段 4（等爬完）
