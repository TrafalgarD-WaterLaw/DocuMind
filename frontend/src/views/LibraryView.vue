<template>
  <div class="library-view">
    <!-- 页头：入库流水线叙事 -->
    <header class="lib-header">
      <div class="lib-title-wrap">
        <h2>知识库 · 入库流水线</h2>
        <p>上传 → 版面解析 → 智能分块 → 向量化 → 假设问题生成 → 可检索</p>
      </div>
      <div class="lib-meta">
        <span class="meta-dot"></span>
        已入库 {{ documents.length }} 个来源 · {{ statsText }}
      </div>
    </header>

    <!-- 上传区 -->
    <section class="lib-upload">
      <el-upload
        class="upload-area"
        drag
        multiple
        :auto-upload="false"
        :show-file-list="false"
        :on-change="handleFilesChange"
        accept=".pdf"
      >
        <div class="upload-content">
          <el-icon :size="44" color="#c9a96e"><UploadFilled /></el-icon>
          <p>拖拽 PDF 文档到此处（可多选）</p>
          <span>Docling 版面分析 · 表格识别 · 公式 OCR，入库后立即可问答</span>
        </div>
      </el-upload>

      <!-- 失败任务重试的隐藏文件选择器（U7c） -->
      <input
        ref="retryInput"
        type="file"
        accept=".pdf"
        style="display: none"
        @change="onRetryFile"
      />

      <!-- 任务队列：每文件一卡，实时状态 -->
      <div v-if="queue.length" class="task-queue">
        <div v-for="t in queue" :key="t.task_id" class="task-card" :class="'task-' + t.status">
          <div class="task-head">
            <span class="task-name" :title="t.file_name">{{ t.file_name }}</span>
            <span class="task-badge" :class="'badge-' + t.status">{{ statusLabel(t.status) }}</span>
          </div>
          <div v-if="t.status !== 'done' && t.status !== 'failed'" class="task-progress">
            <el-progress :percentage="t.progress" :stroke-width="6" :color="'#c9a96e'" />
            <p class="task-stage">{{ t.stage_text }}</p>
          </div>
          <p v-if="t.status === 'failed'" class="task-error">{{ taskErrorText(t) }}</p>
          <div v-if="t.status === 'failed'" class="task-retry-line">
            <el-button size="small" type="primary" plain @click="retryTask(t)">重新上传</el-button>
            <span class="task-retry-hint">解析失败任务可重新选择文件上传</span>
          </div>
          <div v-if="t.status === 'done'" class="task-done-line">
            {{ t.chunks }} 个切片 · {{ t.pages }} 页
            <router-link to="/chat" class="task-ask-link">去提问 →</router-link>
          </div>
        </div>
      </div>
    </section>

    <!-- 文档列表 -->
    <section class="lib-docs">
      <div class="docs-head">
        <h3>已入库文档</h3>
        <el-button size="small" text type="primary" @click="refreshAll">刷新</el-button>
      </div>

      <div v-if="documents.length === 0" class="docs-empty">
        <p>知识库为空，上传第一份文档开始建库</p>
      </div>

      <div v-else class="docs-table">
        <div class="doc-row doc-row--head">
          <span class="col-name">来源</span>
          <span class="col-chunks">切片</span>
          <span class="col-questions">问题</span>
          <span class="col-status">状态</span>
          <span class="col-action">操作</span>
        </div>
        <div v-for="doc in documents" :key="doc.source" class="doc-row">
          <span class="col-name" :title="doc.source">{{ displayName(doc.source) }}</span>
          <span class="col-chunks">{{ doc.chunks }}</span>
          <span class="col-questions">{{ doc.questions }}</span>
          <span class="col-status"><span class="status-dot" :class="'status-' + doc.status" />{{ docStatusLabel(doc.status) }}</span>
          <span class="col-action">
            <el-button size="small" text type="danger" :loading="deleting === doc.source" @click="handleDelete(doc)">删除</el-button>
          </span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { uploadDocument, listUploadTasks, listDocuments, deleteDocument } from '@/api/upload'
import type { UploadTask, DocInfo } from '@/api/upload'
import { fetchStats } from '@/api/stats'

const queue = ref<UploadTask[]>([])
const documents = ref<DocInfo[]>([])
const deleting = ref('')
const statsText = ref('')
const TASK_POLL_MS = 2000  // 任务队列轮询间隔
const ACTIVE_STATUSES = new Set(['queued', 'parsing', 'chunking', 'indexing', 'questions'])
const hasActive = computed(() => queue.value.some(t => ACTIVE_STATUSES.has(t.status)))

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', parsing: '版面解析', chunking: '智能分块',
  indexing: '向量入库', questions: '生成问题', done: '完成', failed: '失败',
}
function statusLabel(s: string): string { return STATUS_LABELS[s] || s }

// 解析失败分类 → 展示文案（后端只返回分类标识,文案由前端映射）
const PARSE_ERROR_TEXT: Record<string, string> = {
  permission: '文件无读取权限，请检查文件权限',
  encrypted: 'PDF 已加密，请上传无密码版本',
  timeout: '解析超时，请重试',
  invalid_pdf: '文件不是有效的 PDF',
  other: '解析失败，请稍后重试',
}
function taskErrorText(t: { stage_text: string; error: string }): string {
  const text = PARSE_ERROR_TEXT[t.stage_text] || '解析失败'
  // other 分类时附原始异常信息（排错用）
  return t.stage_text === 'other' && t.error ? `${text}（${t.error}）` : text
}

function docStatusLabel(s: string): string {
  if (s === 'done') return '已就绪'
  if (s === 'failed') return '失败'
  return STATUS_LABELS[s] || s
}
function displayName(source: string): string {
  // 剥掉时间戳前缀：1234567890_xxx.pdf → xxx.pdf
  return source.replace(/^\d+_/, '')
}

// ── 轮询：有活跃任务时每 2s 拉一次；挂载时恢复 ──
let pollTimer: ReturnType<typeof setInterval> | null = null

async function refreshTasks() {
  try {
    const res = await listUploadTasks()
    queue.value = res.tasks
    if (!hasActive.value && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  } catch { /* 后端不可用时静默，手动刷新兜底 */ }
}

async function refreshDocs() {
  try {
    const res = await listDocuments()
    documents.value = res.documents
    const stats = await fetchStats()
    statsText.value = `共 ${stats.chunks} 切片 · ${stats.questions} 假设问题`
  } catch {
    statsText.value = ''
  }
}

async function refreshAll() {
  await Promise.all([refreshTasks(), refreshDocs()])
  if (hasActive.value && !pollTimer) {
    pollTimer = setInterval(refreshTasks, TASK_POLL_MS)
  }
}

onMounted(refreshAll)
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })

// ── 失败任务重试（U7c）：隐藏文件选择器复用上传逻辑 ──
const retryInput = ref<HTMLInputElement | null>(null)

function retryTask(_t: unknown) {
  // 触发隐藏文件选择器复用上传逻辑（参数仅模板事件传参,未使用）
  retryInput.value?.click()
}

async function onRetryFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) await handleFilesChange({ raw: file })
  input.value = ''
}

// ── 上传：多文件逐个提交（同名提示替换）──
async function handleFilesChange(file: any) {
  const raw = file.raw as File
  if (!raw) return

  const existing = documents.value.find(d => displayName(d.source) === raw.name)
  let replace = false
  if (existing) {
    try {
      await ElMessageBox.confirm(
        `「${raw.name}」已入库（${existing.chunks} 切片），重新上传将替换旧版本？`,
        '同名文档',
        { confirmButtonText: '替换', cancelButtonText: '取消', type: 'warning' },
      )
      replace = true
    } catch {
      return
    }
  }

  try {
    const res = await uploadDocument(raw, { replace })
    // 立即入队显示状态卡
    queue.value.unshift({
      task_id: res.task_id, file_name: res.file_name, source: '',
      status: 'queued', progress: 0, stage_text: '排队中…', error: '',
      pages: 0, blocks: {}, chunks: 0, created_at: Date.now() / 1000, finished_at: 0,
    })
    if (!pollTimer) pollTimer = setInterval(refreshTasks, TASK_POLL_MS)
    ElMessage.success(`已提交「${raw.name}」解析任务`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message || '提交失败')
  }
}

// ── 删除 ──
async function handleDelete(doc: DocInfo) {
  try {
    await ElMessageBox.confirm(
      `删除「${displayName(doc.source)}」的全部切片与假设问题？此操作不可恢复。`,
      '删除文档',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  deleting.value = doc.source
  try {
    const res = await deleteDocument(doc.source)
    documents.value = documents.value.filter(d => d.source !== doc.source)
    ElMessage.success(`已删除 ${res.removed} 个切片`)
    await refreshAll()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  } finally {
    deleting.value = ''
  }
}
</script>

<style scoped lang="less">
.library-view { height: 100%; overflow-y: auto; padding: 28px 32px 48px; }

.lib-header {
  display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 24px;

  .lib-title-wrap {
    h2 {
      font-family: 'STSong', 'SimSun', serif; font-size: 26px; font-weight: 900;
      color: var(--color-primary); letter-spacing: 4px; margin-bottom: 6px;
    }
    p { font-size: 13px; color: var(--color-ink); opacity: 0.5; letter-spacing: 2px; }
  }

  .lib-meta {
    font-size: 12px; color: var(--color-primary); display: flex; align-items: center; gap: 6px;

    .meta-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-gold); }
  }
}

.lib-upload { max-width: 760px; margin-bottom: 32px; }

.upload-content {
  padding: 34px 20px; text-align: center;
  p { margin-top: 12px; font-size: 15px; color: var(--color-ink); font-weight: 600; }
  span { font-size: 12px; color: #999; line-height: 1.8; }
}

:deep(.el-upload-dragger) {
  background: var(--color-card);
  border: 2px dashed rgba(var(--color-gold-rgb), 0.45);
  border-radius: 10px;
  transition: border-color 0.3s;
  &:hover { border-color: var(--color-gold); }
}

.chunk-options {
  margin-top: 12px;
  :deep(.el-collapse-item__header) { font-size: 12px; color: var(--color-primary); }
  .chunk-op-row {
    display: flex; align-items: center; gap: 16px; padding: 4px 0;
    span { font-size: 12px; color: var(--color-ink-muted); width: 70px; flex-shrink: 0; }
    :deep(.el-slider) { flex: 1; }
  }
}

// ── 任务队列卡片 ──
.task-queue { margin-top: 14px; display: flex; flex-direction: column; gap: 10px; }

.task-card {
  padding: 12px 16px;
  background: var(--color-card);
  border: 1px solid rgba(var(--color-gold-rgb), 0.4);
  border-radius: 10px;
  border-left-width: 3px;

  &.task-done { border-left-color: #67c23a; }
  &.task-failed { border-left-color: #f56c6c; }
  &.task-parsing, &.task-chunking, &.task-indexing, &.task-questions, &.task-queued {
    border-left-color: var(--color-gold);
  }
}

.task-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }

.task-name {
  font-size: 13px; font-weight: 600; color: var(--color-ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.task-badge {
  font-size: 11px; font-weight: 600; padding: 2px 10px; border-radius: 10px; flex-shrink: 0;

  &.badge-done { color: #529b2e; background: rgba(103, 194, 58, 0.12); }
  &.badge-failed { color: #f56c6c; background: rgba(245, 108, 108, 0.12); }
  &.badge-queued, &.badge-parsing, &.badge-chunking, &.badge-indexing, &.badge-questions {
    color: var(--color-primary); background: rgba(var(--color-gold-rgb), 0.15);
  }
}

.task-stage { margin: 4px 0 0; font-size: 12px; color: #999; }
.task-error { margin: 4px 0 0; font-size: 12px; color: #f56c6c; }
.task-done-line {
  margin-top: 4px; font-size: 12px; color: var(--color-ink-muted);
  display: flex; align-items: center; gap: 10px;
}
.task-ask-link { color: var(--color-accent); font-weight: 600; text-decoration: none; }

// ── 文档列表 ──
.lib-docs { max-width: 760px; }

.docs-head {
  display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;
  h3 {
    font-family: 'STSong', 'SimSun', serif; font-size: 16px; font-weight: 700;
    color: var(--color-primary); letter-spacing: 2px;
  }
}

.docs-empty {
  padding: 36px 0; text-align: center;
  border: 1px dashed rgba(var(--color-gold-rgb), 0.35); border-radius: 8px;
  color: #b0a08a; font-size: 13px; letter-spacing: 1px;
}

.docs-table {
  border: 1px solid rgba(var(--color-gold-rgb), 0.3); border-radius: 8px;
  overflow: hidden; background: var(--color-card);
}

.doc-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(var(--color-gold-rgb), 0.12);
  font-size: 13px;

  &:last-child { border-bottom: none; }
  &--head {
    background: rgba(var(--color-gold-rgb), 0.1); font-size: 12px;
    font-weight: 600; color: var(--color-primary);
  }
  &:hover:not(.doc-row--head) { background: rgba(var(--color-gold-rgb), 0.04); }
}

.col-name {
  flex: 1; min-width: 0; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; color: var(--color-ink);
}
.col-chunks, .col-questions { width: 56px; text-align: center; color: #999; font-size: 12px; }
.col-status { width: 80px; text-align: center; font-size: 12px; color: var(--color-ink); }
.col-action { width: 60px; text-align: right; }

.status-dot {
  display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 5px;

  &.status-done { background: #67c23a; }
  &.status-failed { background: #f56c6c; }
  &.status-parsing, &.status-chunking, &.status-indexing, &.status-questions, &.status-queued {
    background: var(--color-gold);
  }
}
</style>
