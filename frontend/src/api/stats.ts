import apiClient from './client'
import type { SystemStats } from '@/types/api'

/** 获取系统规模统计（首页驾驶舱数据） */
export async function fetchStats(): Promise<SystemStats> {
  const response = await apiClient.get<SystemStats>('/api/stats')
  return response.data
}
