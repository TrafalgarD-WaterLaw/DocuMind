import type { StreamEvent } from '@/types/api'
import { ndjsonStream } from './stream'

/** 快速问答（单 Agent）——NDJSON 流式（L: 解析统一走 ndjsonStream） */
export function streamChat(
  query: string,
  messages: { role: string; content: string }[] = [],
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  return ndjsonStream('/api/chat', { query, messages }, signal)
}

/** 深度研究（多 Agent 协作） */
export function streamResearch(
  query: string,
  messages: { role: string; content: string }[] = [],
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  return ndjsonStream('/api/research', { query, messages }, signal)
}
