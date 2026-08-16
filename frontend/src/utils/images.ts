import { API_BASE } from '@/api/stream'

/**
 * 后端图片静态服务：后端 image_path 已统一带前缀（/api/uploads/、/api/images/）
 * 或为完整 http(s) URL（河南原图），仅需两分支，无需裸相对补全
 */
export function imageAbsUrl(rel: string): string {
  if (!rel) return ''
  if (rel.startsWith('http://') || rel.startsWith('https://')) return rel
  if (rel.startsWith('/')) return `${API_BASE}${rel}`
  return ''
}
