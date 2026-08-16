/** 后端 API 基础设施（L: API_BASE 与 NDJSON 流解析此前在 chat/vision/组件
 * 三处重复——统一收口） */
import type { StreamEvent } from '@/types/api'

export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5172'

/**
 * NDJSON 流式请求（POST）——逐行解析为 StreamEvent 事件
 * @param url API 路径（如 /api/chat）
 * @param body 请求体（普通对象自动 JSON.stringify；FormData 原样）
 * @param signal 取消信号（AbortController）
 */
export async function* ndjsonStream(
  url: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(`${API_BASE}${url}`, {
    method: 'POST',
    headers:
      body instanceof FormData
        ? { Accept: 'application/x-ndjson' }
        : { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
    body:
      typeof body === 'string' || body instanceof FormData
        ? body
        : JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('Response body is not readable')
  }

  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || '' // 末段可能不完整，留到下次
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        try {
          yield JSON.parse(trimmed) as StreamEvent
        } catch {
          // 非 JSON 行（空行等）静默跳过
        }
      }
    }
    if (buffer.trim()) {
      try {
        yield JSON.parse(buffer.trim()) as StreamEvent
      } catch {
        // 尾段非 JSON 忽略
      }
    }
  } finally {
    reader.releaseLock()
  }
}
