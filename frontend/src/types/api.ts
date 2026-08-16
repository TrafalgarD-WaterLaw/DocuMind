/** Stream event types from the backend */
export enum StreamEventType {
  ZERO_RESULT = 'zero_result',
  REASONING = 'reasoning',
  CONTENT = 'content',
  MARKDOWN_DICT = 'markdown_dict',
  SOURCES = 'sources',
  RECOGNITION = 'recognition',
  ERROR = 'error',
  TRACE = 'trace',
  PIPELINE = 'pipeline',
}

/** 图像识别结果（vision 联动） */
export interface RecognitionEventData {
  result: string
  introduce: string
}

/** 图谱锚定信息（实体 + 关联 + 关系三元组） */
export interface GraphAnchor {
  entity: string
  related: string[]
  links?: { source: string; name: string; target: string }[]
}

/** 一条引用来源（证据锚定） */
export interface SourceItem {
  index: number
  id: string
  source: string
  paths: string[]
  content: string
  /** 文档图片原图（P1-B 契约:带前缀完整路径,/api/uploads/... 或 /api/images/...） */
  image_url?: string
  /** 关联图片（映射表驱动,多图;带 /api/images/ 前缀） */
  images?: string[]
  graph_anchor?: GraphAnchor
}

/** 系统统计（首页驾驶舱） */
export interface SystemStats {
  chunks: number
  questions: number
  graph: { artifacts: number; sites: number; eras: number; kilns: number } | null
  documents: number
}

/** A single event in the NDJSON stream */
export interface StreamEvent {
  type: StreamEventType
  data: unknown
  timestamp: number
}

/** Graph node data from the API */
export interface GraphNodeData {
  name: string
  category: string
  image: string
  introduce: string
  time?: string
  when?: string
  where?: string
}

/** Graph link data from the API */
export interface GraphLinkData {
  source: string
  target: string
  name: string
}

/** Graph data envelope from the API（后端返回 echarts_data/nodes_relation 字段） */
export interface GraphData {
  echarts_data: GraphNodeData[]
  nodes_relation: GraphLinkData[]
}

/** Request payload for knowledge graph search */
export interface KnowledgeSearchRequest {
  node_data: GraphNodeData[]
  link_data: GraphLinkData[]
  node_name: string
  cypher_query: string
}

/** 检索流水线阶段（方案 B：后端实时事件累加） */
export interface PipelineStage {
  stage: string
  status?: string
  name?: string
  hits?: number
  tookMs?: number
  rewrittenQuery?: string
  merged?: number
  sources?: number
  // 专家执行/拒答/分解（步骤卡移除后信息并入 PIPELINE 事件）
  agent?: string
  message?: string
  duration?: number
  count?: number
  subQueries?: string[]
  reason?: string
}

/** 检索诊断（trace 流事件）——一次问答的检索过程 */
export interface RetrievalTrace {
  trace_id: string
  query: string
  // 拆解场景无单一改写词 → null（前端显示"无"）
  rewritten_query: string | null
  crag_triggered: boolean
  paths: Record<string, { hits: number; took_ms: number }>
  path_stats: Record<string, number>
  total_ms: number
  llm_usage: { prompt_tokens?: number; completion_tokens?: number }
}
