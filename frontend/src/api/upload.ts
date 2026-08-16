import apiClient from './client'

export interface UploadTask {
  task_id: string
  file_name: string
  source: string
  status: 'queued' | 'parsing' | 'chunking' | 'indexing' | 'questions' | 'done' | 'failed'
  progress: number
  stage_text: string
  error: string
  pages: number
  blocks: Record<string, number>
  chunks: number
  created_at: number
  finished_at: number
}

export interface DocInfo {
  source: string
  chunks: number
  questions: number
  pages: number
  status: string
  created_at: number
}

/** 上传 PDF → 创建解析任务，立即返回 task_id（进度走任务轮询） */
export async function uploadDocument(
  file: File,
  opts?: { replace?: boolean; chunkSize?: number; overlap?: number },
): Promise<{ task_id: string; file_name: string }> {
  const formData = new FormData()
  formData.append('file', file)
  if (opts?.replace) formData.append('replace', 'true')
  if (opts?.chunkSize) formData.append('chunk_size', String(opts.chunkSize))
  if (opts?.overlap) formData.append('chunk_overlap', String(opts.overlap))
  const response = await apiClient.post<{ task_id: string; file_name: string }>(
    '/api/upload', formData,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60000 },
  )
  return response.data
}

/** 最近上传任务列表（挂载时恢复轮询） */
export async function listUploadTasks(): Promise<{ tasks: UploadTask[] }> {
  const response = await apiClient.get<{ tasks: UploadTask[] }>('/api/upload/tasks')
  return response.data
}

/** 单个任务状态 */
export async function getUploadTask(taskId: string): Promise<UploadTask> {
  const response = await apiClient.get<UploadTask>(`/api/upload/tasks/${taskId}`)
  return response.data
}

/** 列出已入库文档（含切片/问题数） */
export async function listDocuments(): Promise<{ documents: DocInfo[]; count: number }> {
  const response = await apiClient.get<{ documents: DocInfo[]; count: number }>('/api/documents')
  return response.data
}

/** 删除文档来源（chunks + 问题 + 任务记录） */
export async function deleteDocument(source: string): Promise<{ removed: number }> {
  const response = await apiClient.delete(`/api/documents/${encodeURIComponent(source)}`)
  return response.data
}
