# 架构增量治理设计（数据契约 + ingest 管道 + 文档）

日期：2026-08-07
状态：已确认走「增量治理」路线（检索核心不动，评测基线保护）

## 一、治理范围（现状问题 → 治理项）

| 层 | 问题 | 治理 |
|---|---|---|
| 数据 | 文本块无 chunk_type 字段（检索靠 `#图` 隐式约定） | P1-A：chunk_type 全量标记（文本=text，图片=image）+ 迁移脚本 |
| 数据 | image_path 三套语义（无前缀/相对/带前缀） | P1-B：统一为带 `/api/` 前缀 + 前端删裸相对分支 |
| 数据 | source 五种形态无规范 | P1-C：命名规范写入文档 + ingest 管道强制校验（不重命名存量） |
| 数据 | 测试数据残留（4 份 PDF、3 个 Chroma 版本） | P1-D：清理（保留最新 1 份） |
| 代码 | 7 个 import 脚本各写各的 | P2：统一 ingest 管道（BaseIngestor + 注册表，轻量版） |
| 文档 | CLAUDE.md 严重过时（ragpacks/zero_shot_packs/fast.py/config.js 已不存在） | P3：重写 CLAUDE.md |

检索核心（hybrid.py 五路 + RRF + 树剪枝）**不动**——P1-A 的 where 精确匹配不受 text 标记影响；P1-B 只改前端拼接逻辑与存量 metadata。

## 二、P1 数据契约

### P1-A：chunk_type 全量标记
- 现状：图片块 `chunk_type: "image"`；文本块无字段。
- 目标：文本块补 `chunk_type: "text"`；新入库统一由 ingest 管道标记。
- 迁移：一次性脚本 `scripts/migrate_chunk_type.py`——遍历 documents 集合，`chunk_type` 缺失的块批量 update metadata 加 `"chunk_type": "text"`（不重 embedding，Chroma update 仅 metadata）。
- 影响面验证：`where={"chunk_type": "image"}`（hybrid 直检通道）精确匹配不受影响；前端/其他逻辑不读该字段。

### P1-B：image_path 统一语义
- 目标规范：metadata.image_path 一律存**带前缀完整路径**（`/api/images/...` 或 `/api/uploads/...`）；前端 `imageAbsUrl` 只保留两分支（http(s) 原样、`/` 开头直拼），删裸相对分支。
- 迁移：
  - 上传文档图片块（妇好鸮尊 3 块等）：`{source}.images/{name}` → `/api/uploads/{source}.images/{name}`（脚本批量改）
  - 数据集映射表（image_index.json）：值保持相对路径（服务层拼前缀是映射表体系的设计，不迁移）
  - 河南图片块：已带 `/api/images/` 前缀 ✓ 不动
- 前端：`EvidencePanel.vue` imageAbsUrl 删裸相对分支（当前还有 uploads 无前缀路径会走裸相对——迁移后不再有，删除分支 + 注释说明）。

### P1-C：source 命名规范（文档化 + 强制）
规范（写入 CLAUDE.md + ingest 管道校验函数）：
```
{域}-{实体}         文本块    青铜-叩鼎 / 宣德-青花梅瓶 / 河南博物院-妇好墓玉龙
{域}-{实体}#图      图片块    河南博物院-妇好墓玉龙#图（#图 后缀 = 图片块，隔离来源多样性）
{timestamp}_{file}  上传文档  （天然时间戳前缀，不迁移）
```
- 不重命名存量（成本高收益低）；管道校验：新数据源接入必须符合规范（`validate_source()` 拒绝非法格式）。

### P1-D：测试数据清理
- Chroma：删除 1786071360 / 1786072774（旧版妇好鸮尊测试文档 chunks + questions）；保留 1786074778（当前版，含图片块）。
- 磁盘：删 uploads 下 1786070691/1786071360/1786072774 三份 PDF + 旧 images 目录（若存在）。
- docs/test-data/ 保留（源资产）。

## 三、P2 统一 ingest 管道（轻量版）

- 新目录 `Backend/src/services/ingest/`：
  - `base.py`：`BaseIngestor` 抽象——`scan() -> list[RawSource]`、`build_chunks() -> list[dict]`、`load()`（入 vector + mark_bm25_dirty + 更新映射表）、幂等与进度回调
  - `registry.py`：`register(name, ingestor)` / `get_ingestor(name)` / `run(name, **opts)` 入口（CLI: `uv run python -m services.ingest --source X`）
- 现有 7 个脚本**标记 deprecated 不重写**（数据已验证入库，重写回归风险 > 收益）；新管道用于未来数据源（如河南全库扩展、新数据集）。
- 提供 `examples/` 注释性示例（porcelain 风格接入参考）。

## 四、P3 CLAUDE.md 重写

对齐当前真实架构：
- 路由清单：/api/chat、/api/research、/api/upload（+tasks/documents）、/api/knowledge（init/expand/search）、/api/vision/chat、/api/image/recognize、/api/stats、/api/uploads、/api/images、/api/health
- 前端：Vue3 `<script setup lang="ts">` + Pinia + Less；路由 hash（/chat、/library、/knowledge、/deep-qa）
- 数据目录结构：src/data/{chroma, uploads, images, logs, henan_museum.json, henan_images.json, image_index.json}
- 数据契约规范（P1-C 内容）
- ingest 管道说明
- 评测命令：eval/run_eval.py、judge.py
- 常用命令更新（无 ragpacks/indexer.py、无 test_neo4j.py）

## 五、验收标准

1. P1-A：迁移后全库无 chunk_type 缺失的块；`where={chunk_type: "image"}` 检索仍精确命中图片块；60 passed。
2. P1-B：前端构建通过；上传文档图片块 metadata 带 /api/uploads/ 前缀；无裸相对路径残留（grep 验证）。
3. P1-D：Chroma 无 1360/2774 残留；uploads 仅 1 份测试 PDF + images 目录；文档列表干净。
4. P2：`uv run python -m services.ingest --source porcelain` 能跑通（用现有瓷器数据 dry-run 或小型样本验证），不重导真实数据。
5. P3：CLAUDE.md 描述与代码库一致（抽查 5 处：路由/前端/目录/命令/契约）。
6. 全量 pytest 60 passed；前端 build 通过。

## 六、实施顺序

P1-A → P1-B → P1-D → P3 → P2（P2 独立可并行/最后）
