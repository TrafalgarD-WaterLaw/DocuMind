"""应用配置管理——所有可调参数集中于此，从 .env 和环境变量加载"""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """从 .env 和环境变量加载配置"""

    # ── 服务 ──
    host: str = "0.0.0.0"
    port: int = 5172

    # ── LLM ──
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"  # DeepSeek 官方 API
    llm_model: str = "deepseek-v4-flash"  # 官方模型（另有 deepseek-v4-pro）

    # ── 文档图片描述（无 key 时降级为文件名占位）──
    dashscope_api_key: str = ""            # .env: DASHSCOPE_API_KEY（阿里云百炼）
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    image_caption_model: str = "qwen-vl-max-latest"

    # ── Neo4j 图谱 ──
    # 显式 127.0.0.1 而非 localhost——Windows 上 localhost 常解析为 IPv6
    # (::1)，而 Neo4j 默认只监听 IPv4，导致连接被拒
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # ── 路径 ──
    chroma_persist_dir: str = "src/data/chroma"       # 向量库持久化目录
    upload_dir: str = "src/data/uploads"               # 上传文档保存目录
    bge_model_path: str = (                            # 本地 BGE 模型路径（离线加载）
        "D:/cache/modelscope/models/"
        "BAAI--bge-small-zh-v1.5/snapshots/master"
    )
    reranker_model_path: str = (                       # 精排模型（bge-reranker-v2-m3）
        "D:/cache/modelscope/models/"
        "BAAI--bge-reranker-v2-m3/snapshots/master"
    )
    clip_model_path: str = (                           # CLIP 图文模型本地路径
        "D:/cache/modelscope/models/"
        "OFA-Sys--chinese-clip-vit-base-patch16"
    )
    trace_log_dir: str = "src/data/logs"   # 查询轨迹 JSONL 日志目录

    # ── 混合检索参数 ──
    hybrid_path_k: int = 8      # 每路召回数量
    question_path_k: int = 30   # question 路召回数（16152 条问题索引，top-8 太窄）
    hybrid_top_k: int = 8       # RRF 融合后最终返回数量
    rrf_k: int = 60             # RRF 平滑参数
    rrf_graph_weight: float = 0.5  # graph 路权重（扩展词再检索，与 semantic 重叠降权）
    rrf_question_weight: float = 1.0  # question 路权重:Q-to-Q 排序对通用问题形态接近随机,不特殊加权
    # 图片块(图注)RRF 降权:图注块语义相似度升高会挤占文本证据;
    # 0.3 保证文本证据弱时图片块仍可浮上,但不与文本同权竞争
    image_evidence_weight: float = 0.3
    image_chunk_top_k: int = 5      # semantic 路图片块直检补数(树剪枝只搜文本块)
    failure_cooldown_seconds: float = 60.0  # graph/CLIP 加载失败后的冷却期(不重复阻塞重试)
    pipe_poll_interval: float = 0.1  # 流水线事件桥轮询间隔(秒)
    # CLIP 文找图路(第 6 路)权重:视觉相似 ≠ 问题相关(如"汝窑天青釉"
    # 与钧窑釉色近邻混淆),低权重防挤占文本证据;文本证据弱时仍可浮上
    clip_path_weight: float = 0.1
    candidate_pool: int = 64    # 无 rerank 时的候选池(32 太小,单票被截断)
    rerank_candidates: int = 32 # 启用 rerank 时进入精排的候选数
    rerank_path_k: int = 16     # 启用 rerank 时的每路召回数（扩大召回给精排空间）
    use_rerank: bool = False    # cross-encoder 精排:文本场景网格实验全面退化(Recall -7.1pp /
                                # MRR -14%),多模态场景受 4GB 消费级显存限制无法评估——维持禁用
    # vision 识别置信度门控: CLIP 图-图余弦,低于阈值视为库内无相似物,
    # 不拿不确定识别名去检索
    vision_low_conf_threshold: float = 0.75
    clip_retrieval_top_k: int = 5   # CLIP 图文互检每次查询召回图片数（text_search 默认）
    # CLIP 视觉命中进回答上下文的图注块上限（独立图片证据链——图注短,
    # 6 块 ≈ 数百 token,不挤占文本证据窗口）
    clip_evidence_max_blocks: int = 6
    tree_level1_k: int = 5      # 树状剪枝：粗筛数量
    tree_level2_k: int = 10     # 树状剪枝：细搜数量
    tree_max_branches: int = 3  # 树状剪枝：每层保留枝干数

    # ── 上下文组装 ──
    # 块级噪声过滤阈值:RRF 分数分布多路票 ≥0.0308、单票 ≤0.0167,
    # 天然分界 ~0.025——低于此分且单票且排名 > 4 的弱块不送 LLM。
    rrf_score_threshold: float = 0.025
    # 组装时父块内容截断上限（父块本身 ≤1500，一致则等于不截断）
    context_block_chars: int = 1500
    # 送 LLM 的最大块数（复合问题多查询合并可能超限——8 条 × 1500 字
    # ≈ 1.2 万字 ≈ 8-9k token，DeepSeek 窗口内安全）
    context_max_chunks: int = 8

    # ── 假设性问题生成 ──
    hypothesis_batch_size: int = 3   # 每批 chunk 数（LLM 调用粒度）
    questions_per_chunk: int = 3     # 每 chunk 生成问题数

    # ── CRAG 重检索 ──
    crag_enabled: bool = True        # 检索质量评估 → 不足时改写重检索一轮

    # ── 文本实体锚定 ──
    entity_anchor_enabled: bool = True  # 实体名按 source 精确匹配（短条目 embedding 劣势补偿）

    # ── 文档分块 ──
    chunk_size: int = 500
    chunk_overlap: int = 50

    # ── 文档解析 ──
    # 扫描件 OCR：默认开启——文物/古籍领域扫描件 PDF 常见，Docling 在
    # easyocr 未安装时自动降级（warning 提示，不阻塞电子 PDF 解析）
    docling_ocr: bool = True
    # 上传限制（P2 资源上限）——单文件大小上限（字节，默认 100MB）与
    # 并发解析任务上限（Docling 版面分析为 CPU/内存重型，超限排队）
    upload_max_size: int = 100 * 1024 * 1024
    upload_max_concurrency: int = 2

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
