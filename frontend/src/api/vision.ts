import type { StreamEvent } from '@/types/api'
import { ndjsonStream } from './stream'

/** 图像识别 + RAG 联动：上传图片 → 流式问答（L: 解析统一走 ndjsonStream） */
export function streamVision(
  imageFile: File,
  query: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const formData = new FormData()
  formData.append('file', imageFile)
  formData.append('query', query)
  return ndjsonStream('/api/vision/chat', formData, signal)
}
