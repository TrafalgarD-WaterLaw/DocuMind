# ADR:DDD 重构的裁剪与豁免决策(2026-08-14)

> 依据 `docs/ddd-migration-map.md` 执行阶段 1-4 时的三项架构裁决。
> 原则:规则服务于可读性与可扩展性,不为规则本身支付行为风险。

## ADR-1:四层分层按域裁剪(检索域降级)

**决策**:conversation/graph/ingestion 三域套 domain/application/infrastructure/
interfaces 四层;`retrieval/` 保持**单层算法包**;`documents/` 两层层
(application + interfaces);`multimodal/` 维持收拢形态。

**理由**:检索域(六路召回/RRF/树剪枝)是纯算法管道——没有领域规则、
没有持久化选择、没有接口-实现分离的收益点;套四层只产生仪式性目录
与跨层转发,反而稀释"hybrid 是检索核心"的可读性。documents 是纯 CRUD
管理(蓝图"纯 CRUD 模块降级"条款)。RAG 系统的"富领域"在 conversation
(引用归因/拒答/研究计划)与 ingestion(source 契约/8 路联动)——
这两处四层化有真边界收益。

**代价**:面试被问"为什么检索域不四层"——答案即本条理由(算法域
四层化是过度设计,业界公认)。

## ADR-2:模型资源型单例豁免

**决策**:以下三处**豁免**"禁止模块级单例"规则,保持现状:
1. `core.config.settings`——配置对象(规则原文豁免)
2. `core.di.container`——组合根(装配唯一入口,main.py app.state 持有)
3. `multimodal.clip_retrieval` 的类级共享模型 + 模块级 `clip_retriever`
   单例——ChineseCLIP 400MB 权重 + Chroma 索引,类级共享 + 双检锁 +
   失败冷却(实测:DI 化每请求 new 会双加载模型,10-30s 阻塞请求)

**理由**:模型加载是重量级资源获取,不是可变业务状态——与"全局可变
状态"的规则意图(隐藏耦合、难测试)不冲突;测试经 `_ensure()` 可
注入/降级。其余 5 处可变缓存(阶段 2)已全部注入化,规则意图已兑现。

## ADR-3:函数长度豁免清单(20 行规则)

**决策**:以下类别函数豁免 ≤20 行,目标 ≤40 行,超 40 行必须拆(阶段 1/4 已执行):

| 豁免类别 | 例 | 理由 |
|---|---|---|
| async 生成器编排(全程 yield 流事件) | `_answer_flow` / `deep_research` / `_run_experts` | 机械拆 `_step1/_step2` 打碎事件流时序,可读性下降 |
| 流式重试状态机 | `_chat_stream_with_fallback` | 重试分支是整体逻辑,拆开反而要传状态 |
| 长 docstring 纯逻辑函数(注释行不计) | `extract_string_list`(47 行含 18 行 docstring) | 蓝图规则注释不计行 |
| 线性分派/装配(if-ladder + 字段构造) | `_query_odm` / `_format_*` | T1-T6 分派已字典化,剩余是数据组装 |
| 外部契约解析(与三方格式绑定的防御链) | `docling_parser.parse` | 拆解改变异常语义 |

**已执行**:37 个超 40 行函数中 17 个完成拆分(tree/context/hybrid 11 函数/
quick 5/deep 4/graph_query 5/upload_pipeline 4/chunker 3/neo4j_store 3/
synthesizer 2/hypothesis 2);生成器豁免 6 个;20-40 行段 67 个批量豁免
(线性组装/注释密集,硬拆收益为负)。

**执行方式**:行为零变化纯提取;验收 = 三面评测契约 + 185 tests
(因本机 YOLO 训练占机押后,恢复后必须全跑)。
