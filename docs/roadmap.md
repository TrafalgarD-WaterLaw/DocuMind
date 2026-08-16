# DocuMind 后续规划（roadmap 索引）

> 完整分析与方案见 **`docs/rag-optimization-blueprint.md`**（全链路优化蓝图）
> 知识图谱讲解见 **`docs/knowledge-graph-explainer.md`**
> 多模态欠缺盘点见 **`docs/multimodal-rag-gaps.md`**（2026-08-13）

## 已完成（2026-08 时点）

- **检索**：五路混合召回 + 加权 RRF + CRAG + 查询改写 + 意图路由 + 图谱结构化问答（核心集 Recall 100% / MRR 0.71，扩展集 100% / 0.97）
- **入库**：异步任务化上传 + BM25 惰性重建 + 文档管理
- **可观测性**：trace 事件 + 检索诊断面板 + query_trace.jsonl
- **容错**：LLM timeout + 重试 + 流式兜底
- **多模态**：河南图注级图片块（3820）+ 瓷器/青铜器图片映射表（2583 source）+ 上传文档图片
- **CLIP 图文双塔直检**（2026-08-11）：text_search 视觉命中 → 图注块进回答上下文（独立图片证据链，不进 RRF 排序，eval 契约不变）；`eval/clip_image_eval.py` 文找图评测 AnyHit@5 = 92%（12/13，唯一弱项"汝窑天青釉"为釉色近邻合理混淆）；`scripts/clip_text_probe.py` 质量探测工具
- **VLM 图注全量补全**（2026-08-13）：3821 个图片块 100% 有描述（此前 94% 文件名占位）；推理模型 `enable_thinking: false` 关思维链（34s/张→1.4s/张、0 推理 token，已永久写入 image_caption.py）；断点续跑脚本 `scripts/backfill_henan_captions.py`；树剪枝改只搜文本块 + 图片块 RRF 降权 0.3 修复 Recall 回归（100%→96%→100%）
- **全项目审阅修复**（2026-08-13）：后端删除型清理（死文件/9 死依赖/幻影 MySQL 配置/死参数/永真死分支）+ upload 管线下沉 services/ + hybrid.retrieve 拆分 + 接口补齐 + 魔数收敛 + 前端死代码/死类型/死图片清理 + KnowledgeGraphPanel 拆分 + 主题色变量收敛 + tenacity 装饰器统一重试（185 passed）
- **清理**：死路由、reasoning 事件、CORS

## 待办（按蓝图顺序）

- [x] **第 1 步 架构治理**：chunk_type 全量标记 / image_path 统一 / source 规范 / 测试数据清理 / ingest 管道 / CLAUDE.md 重写
- [x] **第 2 步 切片 + 上下文优化**：结构感知切分 + 父子分块 + 块级噪声过滤（MRR 0.684→0.708、faithfulness 69%→71%）
- [x] **第 3 步 重排实验**：网格评测证明大候选池 + cross-encoder 全面退化（Recall -7.1pp / MRR -14% / 耗时 16 倍），维持禁用——详见蓝图第五节
- [x] **第 4 步 多轮记忆摘要压缩**（2026-08-08）：>6 轮旧轮次 LLM 滚动摘要 + LRU 缓存，长对话早期实体可回溯（真实冒烟验证）
- [ ] **第 5 步 可选**：bge-m3 评估、查询分解
- [x] **VLM 图片描述**（2026-08-13 完成）：3821 图注块全量补描述（见"已完成"）
- [x] **多模态欠缺 ① CLIP 文找图进检索排序**（2026-08-13）：`_path_clip` 第 6 路——text_search 视觉命中反查文本/图注块参与 RRF；权重网格实验 0.3→MRR 0.676 回归、0.1→契约恢复（100%/0.693）且"外观相似"类问题真实生效（妇好鸮尊 bm25+clip 双票浮上 top8）；前端流水线加"文找图"路
- [x] **混合模态评测**（2026-08-13）：`eval/vision_eval.py` 15 用例三域，GT 从 image_index.json 映射零标注生成，复用真实 vision 链路（识别→top3 多查询→检索合并），契约 **AnyHit@8 100%（15/15）**、识别正确率 100%（跨照片）；过程发现河南爬虫跨器物重复图（MD5 相同→CLIP 距离 0）数据质量边界
- [x] **多模态域收拢**（2026-08-13）：新建 `services/multimodal/` 包（clip_retrieval/image_index/image_caption 迁入 + evidence.py 证据链抽出 + assets.py 资产门面 register/remove）；upload_pipeline 与 ingest 管道改调门面；185 passed + 检索契约 100%/0.693 + 混合模态契约 15/15 不变
- [ ] **多模态欠缺 ② 剩余**（详见 `docs/multimodal-rag-gaps.md`）：faithfulness 重跑、评测集扩充、grounding 视觉定位
- [x] **生成环节 引用联动 + 图文对照**（2026-08-13）：回答正文 [N] 可点击（store.activeCitation → 面板切轮次 + 证据卡滚动高亮 2.4s）+ hover 弹该源首图缩略图 + 证据卡图-文并排布局（图片列左/文本列右）；imageAbsUrl 提取 utils/images.ts 共用

## 架构审计遗留（2026-08-10，已修 🔴 吞错/状态分叉/注解/上帝类拆分）

- [ ] **scripts 入库绕过 ingest 管道**：`import_bronze/heNan/porcelain_chroma.py` 各自重实现"删旧源→入库"且不调 `mark_bm25_dirty`——历史脚本标 deprecated 不重写，但**重跑任一脚本后需手动触发 BM25 重建**（重启或下一次上传），已入库数据不受影响
- [x] **双套 LLM 重试并存**：`with_llm_retry` 加 `validate` 回调（"调用成功但结果无效"同路重试），hypothesis.py 手写 5 次循环已收敛（2026-08-11，164 passed）
- [ ] **五路全并行化实验**：当前仅 graph/entity 的 LLM 提取与三路重叠（MRR 0.669→0.693）；全五路 `asyncio.gather` + `to_thread` 需先验证 Chroma 跨线程查询安全（hnswlib/sqlite 只读并发），用评测量化收益后再定
- [x] **async 请求路径同步 I/O 残余**（2026-08-13 清账）：knowledge.py 与 graph_query.py 早已 to_thread（盘点确认无残余）；真正的残余是 **RelatorExpert.search_path 同步调用**（deep research 图谱查询）→ to_thread；`tracing.write_trace_jsonl` 缓存文件句柄（不再每次 open/close）；`image_caption.read_bytes` → to_thread

## 明确不做

存储换库 / embedding 微调 / HyDE / 语义缓存 / 多知识库权限 / MinerU
/ 推理并发门控（单 worker + 4GB 显存，bge/CLIP 常驻后并发请求在 GPU
排队即可；演示场景串行请求，加信号量属过度工程——面试口径：模型
常驻内存、GPU 天然串行化推理，瓶颈在 LLM 网络往返而非本地推理）
