# 多轮记忆优化设计（历史摘要压缩）

日期：2026-08-08
前置：第 2 步切片+上下文已完成（评测体系就绪）、第 3 步重排已实验否定
参考：docs/rag-optimization-blueprint.md「八、多轮记忆」

## 一、现状分析（代码实证）

| 位置 | 现状 | 问题 |
|---|---|---|
| `orchestrator._with_history`（L179，静态方法） | 最近 6 轮 × 200 字硬截断 | 第 7 轮起早期信息全丢——"它属于什么朝代"（指 10 轮前提到的器物）无法回溯 |
| `synthesizer.synthesize`（L56-65） | 同样 6 轮 × 200 截断 | deep_research 报告同样丢失早期上下文 |
| 查询改写 `_rewrite_query` | LLM 代词消解（只拼上一轮） | 已解决"它"的消解，但只覆盖**上一轮**——超过 1 轮的实体引用仍断 |

**根因**：无记忆层——历史被"窗口截断"，而非"压缩"。长对话早期实体/问题主线丢失。

## 二、方案设计：滚动摘要（LLM 压缩旧轮次）

```
history ≤ 6 轮:   现状（各截断 200 字）——不引入摘要 LLM 调用
history > 6 轮:   旧轮次 history[:-6] → LLM 压缩为 ≤150 字摘要
                  → 上下文 = 「对话摘要」+ 最近 6 轮（各截断 200）
```

**核心组件 `src/services/conversation_memory.py`**：

```python
def build_context(query: str, history: list[dict] | None, llm) -> str:
    # 1. len(history) ≤ 6 → 现状拼接（零 LLM 调用）
    # 2. 否则: summary = _get_or_summarize(history[:-6], llm)
    #    → "对话摘要：{summary}\n历史对话：\n{最近 6 轮}\n\n当前问题：{query}"
    # 3. 摘要失败/异常 → 回退现状（保持可用）
```

**要点**：
1. **摘要缓存**：内存 LRU（~100 条），key = 旧轮次 (role, content) 序列 hash——同一批旧轮次只调一次 LLM，后续请求秒回
2. **摘要 prompt** `prompts/conversation_summary.md`：提炼「用户关注的实体、问题主线、已确认的事实」≤150 字中文——供长对话回溯早期实体（"之前问的妇好鸮尊…"）
3. **调用点**：`_with_history` 改为实例方法调 build_context（两处：图谱问答 L300 + quick_answer L470）；synthesizer history_text 同样替换（共享模块）
4. **协议不变**：前端 messages 结构零改动（摘要只在后端组装）
5. **缓存失效**：后端重启即失（内存态，与 task_manager 同级，可接受）

## 三、范围

**做**：滚动摘要 + 缓存 + 两处调用点 + 摘要 prompt + 测试。
**不做**：对话状态跟踪（结构化实体记忆）、前端会话管理改造、摘要持久化。

## 四、验收标准

1. **短对话**（≤6 轮）：行为与现状完全一致（不调摘要 LLM）——测试断言 LLM 未被调用
2. **长对话**（>6 轮）：摘要 LLM 恰好调用一次（mock 断言）；上下文 = 摘要 + 最近 6 轮
3. **缓存**：相同旧轮次第二次请求不重复调 LLM（mock 调用计数 = 1）
4. **回退**：摘要 LLM 抛异常 → 上下文 = 现状截断拼接（不报错）
5. **回归**：全量 pytest 通过；eval --retrieval-only 无退化；前端无需改动
6. **真实冒烟**：8 轮对话后问早期实体（如第 2 轮提到的器物），回答能回溯（对比改造前"不知道"）

## 五、实施任务

| 任务 | 内容 |
|---|---|
| M1 | conversation_memory.py（build_context + LRU 缓存）+ conversation_summary.md |
| M2 | orchestrator 接入（_with_history → build_context）+ synthesizer history_text 替换 |
| M3 | 测试（短/长/缓存/回退）+ 全量 pytest + eval 回归 + 真实冒烟 |
