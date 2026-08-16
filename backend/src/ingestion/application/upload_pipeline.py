# -*- coding: utf-8 -*-
"""上传解析管线——阶段编排与业务逻辑（从 api/upload.py 下沉）

api 层只做 HTTP 协议（路由/请求校验/响应组装），管线业务（解析/
分块/图片块构造/实体抽取/问题生成/收尾/删除联动）在此：
- _run_pipeline: 7 阶段编排（解析 → 分块 → 实体 → 入库 → 问题 → 收尾）
- _delete_source: 删除文档的 8 路联动清理（主索引/问题/图片/指纹/映射/CLIP/任务/BM25）
- 图片块构造族: _pair_figure_captions / _build_image_chunks / _dedupe_images
"""
import asyncio
import hashlib
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from core.config import settings
from core.di import container
from interfaces.doc_parser import BlockType, DocParser, ParsedDocument
from ingestion.infrastructure.pypdf_parser import PyPDFParser
from ingestion.infrastructure.indexer import IndexerService
from documents.application.task_manager import TaskStatus, UploadTask, task_manager

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(settings.upload_dir)


def _get_parser() -> DocParser:
    """选择解析器 — 优先 Docling，回退 PyPDF"""
    try:
        from ingestion.infrastructure.docling_parser import DoclingParser

        parser = DoclingParser()
        if parser.available:
            return parser
        logger.info("Docling 未安装，使用 PyPDF 回退")
    except ImportError:
        logger.info("Docling 未安装，使用 PyPDF 回退")
    return PyPDFParser()
def _count_blocks(parsed: ParsedDocument) -> dict[str, int]:
    counts: dict[str, int] = {}
    for b in parsed.blocks:
        key = b.type.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _classify_parse_error(e: Exception) -> str:
    """解析异常 → 错误分类（机器可读,展示文案由前端映射）

    分类: permission / encrypted / timeout / invalid_pdf / other
    原始异常信息保留在任务 error 字段（前端 other 分类时展示）。
    """
    msg = str(e)
    if "PermissionError" in type(e).__name__ or "denied" in msg.lower():
        return "permission"
    if "encrypted" in msg.lower() or "password" in msg.lower():
        return "encrypted"
    if ("Timeout" in type(e).__name__ or "timeout" in msg.lower()
            or "timed out" in msg.lower()):
        return "timeout"
    if "not a valid pdf" in msg.lower() or "invalid" in msg.lower():
        return "invalid_pdf"
    return "other"

def _sources_matching_filename(file_name: str) -> list[str]:
    """找出与原始文件名对应的已入库 source（剥离时间戳前缀后精确匹配）

    含 #图 后缀的图片块 source（{source}#图）一并匹配——替换模式
    需删除旧文档的文本块与图片块。
    """
    sources = container.vector.list_sources()
    matched = []
    for s in sources:
        bare = re.sub(r"^\d+_", "", s)
        if bare == file_name or bare == f"{file_name}#图":
            matched.append(s)
    return matched


def _delete_source(source: str) -> int:
    """删除主索引 + 同步清理问题索引 + 连带删除 {source}#图 图片块，返回删除数

    入参先归一化 removesuffix("#图")——前端可能把 #图 行当独立文档删，
    归一化后一次删干净（文本+图片+指纹+CLIP+映射表），不再产生半删除。
    """
    source = source.removesuffix("#图")
    removed = 0
    # 问题索引全量拉取一次（1.6 万条）后按两个 source 变体过滤——
    # 之前放在循环内会拉两次,删除操作没必要重复全量读
    try:
        q_docs = container.questions.get_all_documents()
    except Exception as e:
        logger.warning(f"问题索引读取失败: {e}")
        q_docs = []
    for src in (source, f"{source}#图"):
        removed += container.vector.delete(src)
        stale = [
            d["id"] for d in q_docs
            if d.get("metadata", {}).get("source", "") == src
        ]
        if stale:
            try:
# 走接口方法（delete_by_ids），不直接摸 collection
                container.questions.delete_by_ids(stale)
            except Exception as e:
                logger.warning(f"问题索引清理失败: {e}")
    container.mark_bm25_dirty()
    task_manager.remove_by_source(source)
    _cleanup_source_images(source)
    from documents.application.hash_index import remove_by_source

    remove_by_source(source)  # 清理指纹映射
    # 图片资产联动（映射表 + clip_images）——收口 multimodal/assets.ImageAssets，
    # 与写入侧 register 对称；内部各自兜底不抛
    from multimodal.assets import ImageAssets

    ImageAssets.remove(source)
    return removed


def _pair_figure_captions(blocks: list) -> dict[tuple[int, int], str]:
    """图注配对：页内 '图N' 开头的文本块 → {(page, 图序号): 图注}

    复用河南爬虫配对思路（crawl_henan_images.py，实测 ~89% 配对率）——
    Docling 块流按阅读顺序，图注通常紧邻图片（"图1  xxx"）。
    按 (page, 图序号) 配对——Docling 导出图
    文件名 fig_{page}_{n}.png 的 n 与之精确对齐，同页多图不张冠李戴。
    """
    captions: dict[tuple[int, int], str] = {}
    for b in blocks:
        if b.type in (BlockType.TEXT, BlockType.LIST, BlockType.HEADING):
            m = re.match(r"^图\s*(\d+)[\s.、:：]*(.{1,80})", b.content.strip())
            if m:
                captions.setdefault((b.page, int(m.group(1))), m.group(2).strip())
    return captions


def _build_image_chunks(
    images: list[str], source: str, page_of: dict[str, int],
    caption: str = "", page_texts: dict[int, str] | None = None,
    captions: dict[tuple[int, int], str] | None = None,
) -> list[dict]:
    """图片 → 入库块（T3：携带 image_path 供前端展示原图）

    content 优先级: 图注（页内"图N"按 (page, 序号) 配对）> VLM 描述
    > 页面上下文（前 120 字）> 文件名——图注让图片块携带图片本身语义，
    可被主题词精确检索。同页多图按 fig_{page}_{n} 序号精确配对。
    """
    chunks = []
    for img in images:
        name = Path(img).name
        page = page_of.get(img, 0)
# 图片文件名 fig_{page}_{n}.png 的 n ↔ 页内"图N"图注精确对齐
        m_fig = re.search(r"fig_(\d+)_(\d+)", name)
        n = int(m_fig.group(2)) if m_fig else 0
        fig = (captions or {}).get((page, n), "")
        if fig:
            content = f"【文档图片·第{page}页】{fig}"
            if caption:
                content += f"。{caption}"
        elif caption:
            content = f"【文档图片·第{page}页】{caption}"
        else:
            context = ""
            if page_texts:
                context = (page_texts.get(page) or "").strip()
            if context:
                content = f"【文档图片·第{page}页】{context[:120]}"
            else:
                content = f"【文档图片·第{page}页】{name}"
        # source 加 #图 后缀（P1-C 契约）：图片块与文本块来源多样性隔离，
        # 检索 max_per_source 按 source 独立计数（同河南图片块体系）
        chunks.append({
            "chunk_id": str(uuid.uuid4()),
            "content": content,
            "metadata": {
                "source": f"{source}#图",
                "image_path": f"/api/uploads/{source}.images/{name}",
                "chunk_type": "image",
            },
        })
    return chunks


def _cleanup_source_images(source: str) -> None:
    """删除 source 关联的图片目录（删除文档/替换/失败清理用）"""
    shutil.rmtree(UPLOAD_DIR / f"{source}.images", ignore_errors=True)


def _dedupe_images(
    images: list[str], page_of: dict[str, int],
) -> tuple[list[str], dict[str, int]]:
    """同文档图片内容去重（U7b）——SHA-256,重复图/相同 logo 只保留第一张

    同步文件读,由调用方经 asyncio.to_thread 执行。
    """
    seen_hashes: set[str] = set()
    unique_images: list[str] = []
    page_of_unique: dict[str, int] = {}
    for img in images:
        try:
            h = hashlib.sha256(Path(img).read_bytes()).hexdigest()
        except OSError:
            h = ""
        if h and h in seen_hashes:
            continue
        if h:
            seen_hashes.add(h)
        unique_images.append(img)
        page_of_unique[img] = page_of.get(img, 0)
    return unique_images, page_of_unique


async def _parse_document(
    task_id: str, file_path: Path, tm: Any, timings: dict[str, float],
) -> ParsedDocument:
    """阶段 1:解析 PDF（Docling 优先;同步解析经 to_thread,不阻塞事件循环）"""
    _t0 = time.perf_counter()
    tm.update_task(task_id, status=TaskStatus.PARSING, progress=10,
                   stage_text="版面解析中…")
    parser = _get_parser()
    parsed: ParsedDocument = await asyncio.to_thread(
        parser.parse, str(file_path)
    )
    tm.update_task(task_id, progress=40,
                   pages=parsed.metadata.get("pages", 0),
                   blocks=_count_blocks(parsed))
    timings["parse_ms"] = round((time.perf_counter() - _t0) * 1000)
    return parsed


async def _split_chunks(
    parsed: ParsedDocument, source: str, chunk_size: int, chunk_overlap: int,
) -> tuple[list[dict], list[dict]]:
    """分块:Docling 块流 → 结构感知父子切分;无块流回退 langchain 通用切分"""
    if getattr(parsed, "blocks", None):
        from ingestion.infrastructure.chunker import chunk_document

        # 同步切分(句子边界/父子构造,秒级)——to_thread 不阻塞事件循环
        cr = await asyncio.to_thread(chunk_document, parsed, source=source)
        return cr.children, cr.parents
    indexer = IndexerService()
    chunks = indexer.load_chunks_from_text(
        parsed.markdown,
        source=source,
        chunk_size=chunk_size or settings.chunk_size,
        chunk_overlap=chunk_overlap or settings.chunk_overlap,
    )
    children = [
        {
            "chunk_id": str(uuid.uuid4()),
            "content": c.page_content,
            "metadata": {**c.metadata, "chunk_type": "text"},
        }
        for c in chunks
    ]
    return children, []


async def _build_image_chunks_with_captions(
    task_id: str, parsed: ParsedDocument, source: str, tm: Any,
) -> list[dict]:
    """图片 → 入库块:VLM 描述(并行限流 3)→ 内容去重 → 图注配对 → 块

    返回图片块列表;无图片返回空。VLM 描述按去重后的原始索引对齐合并。
    """
    if not getattr(parsed, "images", None):
        return []
    from multimodal.image_caption import NoopCaptioner

    if not isinstance(container.captioner, NoopCaptioner):
        tm.update_task(task_id, progress=47, stage_text="生成图片描述中…")
    sem = asyncio.Semaphore(3)

    async def _caption_one(img: str) -> str:
        async with sem:
            return await container.captioner.caption(Path(img))

    cap_results = await asyncio.gather(
        *[_caption_one(img) for img in parsed.images]
    )
    # 页码从 Docling 导出文件名解析真实页码(fig_{page}_{n}.png)
    page_of: dict[str, int] = {}
    for img in parsed.images:
        m = re.search(r"fig_(\d+)_", Path(img).name)
        page_of[img] = int(m.group(1)) if m else 0
    # 页面文本映射:拼接图片所在页的文本块(contextual chunk 上下文)
    page_texts: dict[int, str] = {}
    for b in parsed.blocks:
        if (b.type in (BlockType.TEXT, BlockType.HEADING, BlockType.LIST)
                and b.content.strip()):
            prev = page_texts.get(b.page, "")
            if len(prev) < 300:
                page_texts[b.page] = prev + b.content.strip() + " "
    # 图注配对(页内"图N"caption——图片块语义直追河南图注级)
    captions = _pair_figure_captions(parsed.blocks)
    # 内容去重(SHA-256,重复图只保留第一张;同步文件读经 to_thread)
    unique_images, page_of_unique = await asyncio.to_thread(
        _dedupe_images, parsed.images, page_of
    )
    image_chunks = _build_image_chunks(
        unique_images, source, page_of_unique, caption="",
        page_texts=page_texts, captions=captions,
    )
    # 逐图合并描述:cap_results 按 parsed.images 顺序,去重后按原始索引对齐
    orig_index = {img: i for i, img in enumerate(parsed.images)}
    for i, chunk in enumerate(image_chunks):
        orig = orig_index.get(unique_images[i], i)
        if orig < len(cap_results) and cap_results[orig]:
            if f"。{cap_results[orig]}" not in chunk["content"]:
                chunk["content"] += f"。{cap_results[orig]}"
    return image_chunks


async def _build_doc_dicts(
    task_id: str, parsed: ParsedDocument, source: str, file_name: str,
    file_path: Path, chunk_size: int, chunk_overlap: int, tm: Any,
) -> tuple[list[dict], list[dict]]:
    """阶段 2:分块 + 图片块 + 文档元数据——返回 (全部块, 图片块)

    ① 分块:子块(检索粒度,chunk_type=text)+ 父块(is_parent,送 LLM)
    ② 图片块:VLM 描述 + 图注配对 + 去重(chunk_type=image,source 带 #图)
    ③ 文档元数据注入:file_name/uploaded_at/file_size
    """
    tm.update_task(task_id, status=TaskStatus.CHUNKING, progress=45,
                   stage_text="智能分块中…")
    child_chunks, parent_chunks = await _split_chunks(
        parsed, source, chunk_size, chunk_overlap
    )
    doc_dicts = [
        {
            "chunk_id": c.get("chunk_id", str(uuid.uuid4())),
            "content": c.get("content", ""),
            "metadata": {**c.get("metadata", {}), "chunk_type": "text"},
        }
        for c in child_chunks
    ]
    doc_dicts.extend(
        {
            "chunk_id": p.get("chunk_id", str(uuid.uuid4())),
            "content": p.get("content", ""),
            "metadata": {**p.get("metadata", {}), "chunk_type": "text",
                         "is_parent": True},
        }
        for p in parent_chunks
    )
    image_chunks = await _build_image_chunks_with_captions(
        task_id, parsed, source, tm
    )
    doc_dicts.extend(image_chunks)
    # 文档级元数据:所有块携带文档身份(检索过滤/展示维度)
    doc_meta = {
        "file_name": file_name,
        "uploaded_at": int(time.time()),
        "file_size": file_path.stat().st_size,
    }
    for d in doc_dicts:
        d["metadata"].update(doc_meta)
    return doc_dicts, image_chunks


async def _extract_doc_entities(file_name: str, doc_dicts: list[dict]) -> None:
    """LLM 提 ≤5 实体 → 注入全部块 metadata（entity 路按数组匹配;失败/空不阻断）"""
    try:
        from retrieval.entity_anchor import extract_entities

        full_text = "\n".join(d["content"] for d in doc_dicts
                              if d["metadata"].get("chunk_type") != "image")
        entities = await extract_entities(full_text, container.llm)
        if entities:
            for d in doc_dicts:
                d["metadata"]["entities"] = entities
            logger.info("文档实体抽取: %s → %s", file_name, entities)
    except Exception as e:
        logger.warning(f"文档实体抽取失败（不阻断）: {e}")


async def _generate_questions(
    task_id: str, doc_dicts: list[dict], tm: Any,
) -> bool:
    """阶段 5:假设问题生成（失败不阻断,文档已可检索）——返回是否失败"""
    tm.update_task(task_id, status=TaskStatus.QUESTIONS, progress=62,
                   stage_text="生成假设问题中…")

    def on_progress(done: int, total: int) -> None:
        pct = 62 + int(done / max(total, 1) * 33)
        tm.update_task(task_id, progress=min(pct, 95),
                       stage_text=f"生成假设问题 {done}/{total} 批…")

    try:
        from retrieval.hypothesis import build_question_documents

        await build_question_documents(
            container.llm,
            [
                {"id": d["chunk_id"], "content": d["content"],
                 "metadata": d["metadata"]}
                for d in doc_dicts
                if d["metadata"].get("chunk_type") != "image"
                and not d["metadata"].get("is_parent")  # 父块不生成问题
            ],
            container.questions,
            skip_existing=True, on_progress=on_progress,
        )
        return False
    except Exception:
        logger.exception("问题生成失败（文档已可检索）")
        tm.update_task(task_id, progress=95,
                       stage_text="问题生成失败，文档已可检索")
        return True


async def _post_ingest(source: str, image_chunks: list[dict], sha256: str) -> bool:
    """入库收尾:CLIP 图片增量(await 同步完成)+ 指纹登记(重复上传检测)

    返回 CLIP 增量是否成功(await 同步完成——上传结束即一致,
    失败时明确提示图找文不完整)。
    """
    clip_ok = True
    if image_chunks:
        try:
            from multimodal.assets import ImageAssets

            clip_paths, clip_urls = [], []
            for ch in image_chunks:
                p = ch["metadata"].get("image_path", "")
                if p.startswith("/api/uploads/"):
                    rel = UPLOAD_DIR / p.removeprefix("/api/uploads/")
                    if rel.exists():
                        clip_paths.append(str(rel))
                        clip_urls.append(p)
            if clip_paths:
                # 资产门面 register:映射表合并 + CLIP 增量一处完成
                added = await ImageAssets.register(source, clip_paths, clip_urls)
                clip_ok = added > 0
                logger.info(f"CLIP 增量完成: {added}/{len(clip_paths)} 张图")
        except Exception as e:
            clip_ok = False
            logger.warning(f"CLIP 增量失败(图找文不完整): {e}")
    if sha256:
        from documents.application.hash_index import register

        register(sha256, source)
    return clip_ok


async def _run_pipeline(
    task_id: str,
    file_path: Path,
    file_name: str,
    source: str,
    replace: bool,
    chunk_size: int,
    chunk_overlap: int,
    sha256: str = "",
) -> None:
    """上传解析管线:解析 → 分块 → 实体 → 入库 → 问题生成（后台任务执行）

    每阶段模式一致:更新任务状态/进度 → 干活(to_thread 不阻塞事件循环)
    → 报结果 → 记耗时。失败统一收尾:清理残留 + 分类错误信息。
    """
    tm = task_manager
    timings: dict[str, float] = {}  # 分阶段耗时(前端展示 + 性能分析)
    try:
        # 1. 解析 PDF
        parsed = await _parse_document(task_id, file_path, tm, timings)

        # 2. 分块 + 图片块 + 文档元数据
        doc_dicts, image_chunks = await _build_doc_dicts(
            task_id, parsed, source, file_name, file_path,
            chunk_size, chunk_overlap, tm,
        )
        total_chunks = len(doc_dicts)

        # 3. 文档实体抽取(注入块 metadata)
        await _extract_doc_entities(file_name, doc_dicts)

        # 4. 入库(同步向量化经 to_thread)——替换模式先建后删
        await _index_documents(task_id, doc_dicts, total_chunks, tm, timings)

        # 5. 同名替换:入库成功后删旧 source(失败由 except 清理,旧文档不动)
        if replace:
            _replace_old_sources(file_name)

        # 6. 假设问题生成(失败不阻断)
        _t0 = time.perf_counter()
        questions_failed = await _generate_questions(task_id, doc_dicts, tm)
        timings["questions_ms"] = round((time.perf_counter() - _t0) * 1000)

        # 7. 收尾:CLIP 增量(await 同步,失败提示图找文不完整)+ 指纹登记
        await _finish_pipeline(
            task_id, source, image_chunks, sha256, questions_failed,
            total_chunks, file_name, tm, timings,
        )
    except Exception as e:
        _cleanup_failed_pipeline(task_id, source, file_path, e, tm)


async def _index_documents(
    task_id: str, doc_dicts: list[dict], total_chunks: int,
    tm: Any, timings: dict[str, float],
) -> None:
    """阶段 4 入库(同步向量化经 to_thread)——替换模式先建后删:

    入库成功后才删旧 source——失败时旧文档完好;source 名带时间戳
    前缀,新旧天然不冲突。
    """
    _t0 = time.perf_counter()
    tm.update_task(task_id, status=TaskStatus.INDEXING, progress=55,
                   stage_text="向量化入库中…")
    await asyncio.to_thread(container.vector.add_documents, doc_dicts)
    container.mark_bm25_dirty()  # BM25 下次检索前惰性重建
    tm.update_task(task_id, progress=60, chunks=total_chunks)
    timings["index_ms"] = round((time.perf_counter() - _t0) * 1000)


def _replace_old_sources(file_name: str) -> None:
    """阶段 5 同名替换:删除该文件名的旧 source（入库成功后执行）"""
    for old in _sources_matching_filename(file_name):
        _delete_source(old)
        logger.info(f"替换模式：已删除旧来源 {old}")


async def _finish_pipeline(
    task_id: str, source: str, image_chunks: list[dict], sha256: str,
    questions_failed: bool, total_chunks: int, file_name: str,
    tm: Any, timings: dict[str, float],
) -> None:
    """阶段 7 收尾:CLIP 增量(await 同步,失败提示图找文不完整)+ 指纹登记"""
    clip_ok = await _post_ingest(source, image_chunks, sha256)
    stage_text = (
        "问题生成失败，文档已可检索" if questions_failed
        else "CLIP 增量失败，图找文不完整" if not clip_ok
        else "入库完成"
    )
    tm.update_task(task_id, status=TaskStatus.DONE, progress=100,
                   stage_text=stage_text, timings=timings)
    logger.info(f"上传管线完成: {file_name} ({total_chunks} chunks) "
                f"耗时 {timings}")


def _cleanup_failed_pipeline(
    task_id: str, source: str, file_path: Path, error: Exception, tm: Any,
) -> None:
    """失败统一收尾:清理残留 + 分类错误信息（机器可读,展示文案前端映射）

    清理残留:已入库的新 chunks(先建后删后,失败可能发生在入库之后)+
    上传文件与图片目录(不留垃圾)。
    """
    logger.exception("上传管线失败")
    try:
        container.vector.delete(source)
    except Exception:
        pass
    try:
        file_path.unlink(missing_ok=True)
    except Exception:
        pass
    _cleanup_source_images(source)
    tm.update_task(task_id, status=TaskStatus.FAILED, error=str(error),
                   stage_text=_classify_parse_error(error))

