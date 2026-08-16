import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { nanoid } from 'nanoid'
import type { ChatMessage } from '@/types/domain'
import {
  StreamEventType,
  type PipelineStage,
  type RecognitionEventData,
  type RetrievalTrace,
  type SourceItem,
  type StreamEvent,
} from '@/types/api'
import { streamChat, streamResearch } from '@/api/chat'
import { streamVision } from '@/api/vision'

export interface Session {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
}

const STORAGE_KEY = 'artifact-chat-sessions'

function loadSessions(): Session[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const sessions: Session[] = raw ? JSON.parse(raw) : []
    // F1: 流式中途刷新会持久化 isStreaming:true 的空消息——流已死，
    // 刷新后永久卡骨架屏（数据损坏无法自愈）。加载时降级为普通消息。
    for (const s of sessions) {
      for (const m of s.messages) {
        if (m.role === 'assistant' && m.isStreaming) {
          m.isStreaming = false
        }
      }
    }
    return sessions
  } catch { return [] }
}

function saveSessions(sessions: Session[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions))
}

/** 流事件归约状态（applyStreamEvent 的累计上下文） */
interface StreamState {
  content: string
  reasoning: string
  sources: SourceItem[]
}

/**
 * 单个流事件 → 更新助手消息（纯函数——可独立单测）
 *
 * 流式期间 assistantMessage 恒为消息列表最后一条（无并发写入），
 * 直接引用消除 session! 非空断言。
 */
function applyStreamEvent(event: StreamEvent, msg: ChatMessage, st: StreamState): void {
  switch (event.type) {
    case StreamEventType.CONTENT:
      st.content += String(event.data)
      msg.content = st.content
      break
    case StreamEventType.REASONING:
      st.reasoning += String(event.data)
      break
    case StreamEventType.SOURCES: {
      const items = (event.data as { items: SourceItem[] }).items
      if (Array.isArray(items)) {
        st.sources.length = 0
        st.sources.push(...items)
        msg.sources = [...st.sources]
      }
      break
    }
    case StreamEventType.RECOGNITION: {
      const recog = event.data as RecognitionEventData
      if (recog && recog.result) msg.recognition = recog
      break
    }
    case StreamEventType.ERROR:
      st.content += `\n\n> ⚠️ ${String(event.data)}`
      msg.content = st.content
      break
    case StreamEventType.MARKDOWN_DICT: {
      // 结构化思维导图数据（深度模式的研究计划/专家分工）→ 存消息供侧栏渲染
      const md = event.data as { mode: string; sections: { title: string; content: string }[] }
      if (md && md.sections) msg.markdownDict = md
      break
    }
    case StreamEventType.PIPELINE: {
      // 方案 B: 检索流水线实时事件 → 按轮次消息累加
      // （含 expert 专家执行 / refuse 拒答 / decompose 分解——步骤卡
      //   移除后信息并入此处）
      const data = event.data as Record<string, unknown> | null
      if (!data || typeof data.stage !== 'string') break
      const stage: PipelineStage = {
        stage: data.stage,
        status: data.status ? String(data.status) : undefined,
        name: data.name ? String(data.name) : undefined,
        hits: typeof data.hits === 'number' ? data.hits : undefined,
        tookMs: typeof data.took_ms === 'number' ? data.took_ms : undefined,
        rewrittenQuery: data.rewritten_query
          ? String(data.rewritten_query)
          : undefined,
        merged: typeof data.merged === 'number' ? data.merged : undefined,
        sources: typeof data.sources === 'number' ? data.sources : undefined,
        agent: data.agent ? String(data.agent) : undefined,
        message: data.message ? String(data.message) : undefined,
        duration: typeof data.duration === 'number' ? data.duration : undefined,
        count: typeof data.count === 'number' ? data.count : undefined,
        subQueries: Array.isArray(data.sub_queries)
          ? (data.sub_queries as string[])
          : undefined,
        reason: data.reason ? String(data.reason) : undefined,
      }
      if (!msg.pipeline) msg.pipeline = []
      msg.pipeline.push(stage)
      break
    }
    case StreamEventType.TRACE: {
      const trace = event.data as RetrievalTrace
      if (trace) msg.trace = trace
      break
    }
    // ZERO_RESULT 是检索统计事件（检索 N 条/上下文 M 条）,非"零结果"
    // 通知——拒答走 refuse pipeline 阶段,此处不渲染
    case StreamEventType.ZERO_RESULT:
      break
  }
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<Session[]>(loadSessions())
  const activeId = ref<string>(sessions.value[0]?.id || '')
  const isStreaming = ref(false)
  const deepMode = ref(false)
  const abortController = ref<AbortController | null>(null)
  // 引用聚焦（点击回答正文 [N] 触发）——SidePanel 切到对应轮次、
  // EvidencePanel 滚动定位并高亮对应证据卡；ts 保证重复点击同号也重触发
  const activeCitation = ref<{ msgId: string; index: number; ts: number } | null>(null)

  const activeSession = computed(() =>
    sessions.value.find(s => s.id === activeId.value)
  )
  // L: 纯 getter computed（删除空 setter——无任何写入方）
  const messages = computed(() => activeSession.value?.messages || [])

  function persistWithAutoTitle() {
    saveSessions(sessions.value)
    // Auto-title: use first user message
    for (const s of sessions.value) {
      if (s.title === '新对话') {
        const firstUser = s.messages.find(m => m.role === 'user')
        if (firstUser) {
          s.title = firstUser.content.slice(0, 30) + (firstUser.content.length > 30 ? '...' : '')
        }
      }
    }
  }

  function ensureSession() {
    if (!activeId.value || !sessions.value.find(s => s.id === activeId.value)) {
      const id = nanoid()
      sessions.value.unshift({ id, title: '新对话', messages: [], createdAt: Date.now() })
      activeId.value = id
      persistWithAutoTitle()
    }
  }

  function newSession() {
    const id = nanoid()
    sessions.value.unshift({ id, title: '新对话', messages: [], createdAt: Date.now() })
    activeId.value = id
    persistWithAutoTitle()
  }

  function switchSession(id: string) {
    activeId.value = id
  }

  function deleteSession(id: string) {
    const idx = sessions.value.findIndex(s => s.id === id)
    if (idx === -1) return
    sessions.value.splice(idx, 1)
    if (activeId.value === id) {
      activeId.value = sessions.value[0]?.id || ''
      if (!activeId.value) newSession()
    }
    persistWithAutoTitle()
  }

  function addMessage(message: ChatMessage, targetSessionId?: string) {
    const sid = targetSessionId || activeId.value
    if (!sid) return
    const session = sessions.value.find(s => s.id === sid)
    if (session) {
      session.messages.push(message)
      persistWithAutoTitle()
    }
  }

  async function sendQuery(query: string, imageFile?: File) {
    if ((!query.trim() && !imageFile) || isStreaming.value) return
    ensureSession()
    // Capture session ID to prevent race condition during streaming
    const sessionId = activeId.value

    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return

    const userMessage: ChatMessage = {
      id: nanoid(), role: 'user', content: query.trim(), timestamp: Date.now(),
    }
    session.messages.push(userMessage)

    const assistantMessage: ChatMessage = {
      id: nanoid(), role: 'assistant', content: '', timestamp: Date.now(), isStreaming: true,
    }
    session.messages.push(assistantMessage)
    persistWithAutoTitle()

    isStreaming.value = true
    const controller = new AbortController()
    abortController.value = controller

    const st: StreamState = { content: '', reasoning: '', sources: [] }

    try {
      const history = session.messages.slice(-20).map(m => ({ role: m.role, content: m.content }))
      const signal = controller.signal
      const generator = imageFile
        ? streamVision(imageFile, query.trim(), signal)
        : deepMode.value
          ? streamResearch(query, history, signal)
          : streamChat(query, history, signal)

      for await (const event of generator) {
        if (controller.signal.aborted) break
        applyStreamEvent(event, assistantMessage, st)
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        st.content += `\n\n[Error: ${error instanceof Error ? error.message : String(error)}]`
        assistantMessage.content = st.content
      }
    } finally {
      if (assistantMessage.isStreaming) {
        assistantMessage.isStreaming = false
        assistantMessage.content = st.content
        assistantMessage.reasoning = st.reasoning || undefined
        assistantMessage.sources = st.sources.length > 0 ? st.sources : undefined
      }
      isStreaming.value = false
      abortController.value = null
      persistWithAutoTitle()
    }
  }

  function stopGeneration() {
    abortController.value?.abort()
    abortController.value = null
    isStreaming.value = false
  }

  function clearMessages() {
    stopGeneration()
    const session = sessions.value.find(s => s.id === activeId.value)
    if (session) {
      session.messages = []
      session.title = '新对话'
      persistWithAutoTitle()
    }
  }

  function toggleDeepMode() { deepMode.value = !deepMode.value }

  function focusCitation(msgId: string, index: number) {
    activeCitation.value = { msgId, index, ts: Date.now() }
  }

  return {
    sessions, activeId, messages, isStreaming, deepMode, activeSession,
    activeCitation, focusCitation,
    sendQuery, stopGeneration, clearMessages, addMessage,
    toggleDeepMode, newSession, switchSession, deleteSession,
  }
})
