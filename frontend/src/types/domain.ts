import type {
  PipelineStage,
  RecognitionEventData,
  RetrievalTrace,
  SourceItem,
} from './api'

/** A chat message in the conversation */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 用户上传的图片（dataURL，vision 多模态问答时携带） */
  image?: string
  reasoning?: string
  /** 证据来源引用（sources 流事件收集） */
  sources?: SourceItem[]
  /** 检索流水线实时阶段（方案 B） */
  pipeline?: PipelineStage[]
  /** 图像识别结果（vision 联动） */
  recognition?: RecognitionEventData
  /** 结构化思维导图数据（markdown_dict 流事件） */
  markdownDict?: {
    mode: string
    sections: { title: string; content: string }[]
    related_questions?: string[]
  }
  /** 检索诊断（trace 流事件） */
  trace?: RetrievalTrace
  timestamp: number
  isStreaming?: boolean
}
