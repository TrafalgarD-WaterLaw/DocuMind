import apiClient from './client'
import type { GraphNodeData, GraphLinkData, GraphData, KnowledgeSearchRequest } from '@/types/api'

/** Initialize the knowledge graph */
export async function initGraph(): Promise<GraphData> {
  const response = await apiClient.get<GraphData>('/api/knowledge/init')
  return response.data
}

/** Expand a node */
export async function expandNode(req: KnowledgeSearchRequest): Promise<GraphData> {
  const response = await apiClient.post<GraphData>('/api/knowledge/expand', req)
  return response.data
}

/** Search the graph */
export async function searchGraph(req: KnowledgeSearchRequest): Promise<GraphData> {
  const response = await apiClient.post<GraphData>('/api/knowledge/search', req)
  return response.data
}
