<template>
  <div class="pipeline-panel">
    <!-- 查询与改写 -->
    <div class="pp-row pp-query">
      <span class="pp-label">问题</span>
      <span class="pp-text">{{ query || '（等待输入）' }}</span>
    </div>
    <div v-if="rewriteText" class="pp-row pp-rewrite">
      <span class="pp-label">改写</span>
      <span class="pp-text">{{ rewriteText }}</span>
    </div>

    <!-- 复合问题分解（步骤卡移除后并入流水线面板） -->
    <div v-if="decomposeStage" class="pp-row pp-decompose">
      <span class="pp-icon">✂</span>
      <span class="pp-name">复合问题</span>
      <span class="pp-nums pp-decompose-sub">
        拆分为 {{ decomposeStage!.count }} 个子查询
        <span v-if="decomposeStage!.subQueries?.length" class="pp-decompose-list">
          ：{{ decomposeStage!.subQueries.join(' ｜ ') }}
        </span>
      </span>
    </div>

    <!-- 专家执行（深度研究模式；原"研究过程"卡信息迁入此处） -->
    <template v-if="expertStages.length">
      <div class="pp-expert-title">👥 专家执行</div>
      <div
        v-for="(s, i) in expertStages"
        :key="i"
        class="pp-row pp-expert"
        :class="s.status"
      >
        <span class="pp-icon">{{ expertIcon(s.agent) }}</span>
        <span class="pp-name">{{ expertName(s.agent) }}</span>
        <span class="pp-dot" :class="s.status" />
        <span class="pp-nums">
          {{ s.status === 'running' ? '执行中…' : s.status === 'done' ? '完成' : '失败' }}
          <template v-if="s.duration != null"> · {{ s.duration }}s</template>
        </span>
      </div>
    </template>

    <!-- 六路检索 -->
    <div
      v-for="p in PATH_ORDER"
      :key="p"
      class="pp-row pp-path"
      :class="pathClass(p)"
      @click="togglePath(p)"
    >
      <span class="pp-icon">{{ PATH_ICONS[p] }}</span>
      <span class="pp-name">{{ PATH_LABELS[p] }}</span>
      <span class="pp-dot" :class="pathClass(p)" />
      <span class="pp-nums">
        {{ pathStage(p)?.hits ?? '–' }} 条
        <template v-if="pathStage(p)?.tookMs != null">· {{ fmtMs(pathStage(p)!.tookMs!) }}</template>
      </span>
      <span class="pp-chevron">{{ expanded === p ? '▾' : '▸' }}</span>
      <!-- 展开详情 -->
      <div v-if="expanded === p" class="pp-detail" @click.stop>
        <div class="pp-detail-line">
          命中 {{ pathStage(p)?.hits ?? 0 }} 条 · 耗时 {{ fmtMs(pathStage(p)?.tookMs || 0) }}
        </div>
        <div v-if="pathSources(p).length" class="pp-detail-srcs">
          <div v-for="(s, si) in pathSources(p)" :key="si" class="pp-detail-src">
            <span class="pp-detail-srcname">{{ s.source }}</span>
            <span class="pp-detail-srccontent">{{ (s.content || '').slice(0, 60) }}</span>
          </div>
        </div>
        <div v-else class="pp-detail-line">未命中来源</div>
      </div>
    </div>

    <!-- 融合与生成 -->
    <div class="pp-row pp-fuse" :class="{ done: fuseStage?.merged != null }">
      <span class="pp-icon">⚖</span>
      <span class="pp-name">RRF 融合</span>
      <span class="pp-nums">
        <template v-if="fuseStage?.merged != null">
          {{ fuseStage!.merged }} 候选 · {{ fuseStage!.sources }} 来源
        </template>
        <template v-else>等待…</template>
      </span>
    </div>
    <div class="pp-row pp-gen" :class="genClass">
      <span class="pp-icon">✒</span>
      <span class="pp-name">LLM 生成</span>
      <span class="pp-nums">
        <span v-if="genStarted" class="pp-bar" />
        <template v-else>等待…</template>
      </span>
    </div>

    <!-- 拒答（知识库未覆盖——原步骤卡"拒答原因"迁入此处） -->
    <div v-if="refuseStage" class="pp-row pp-refuse">
      <span class="pp-icon">🚫</span>
      <span class="pp-name">已拒答</span>
      <span class="pp-nums">{{ refuseStage!.reason }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { PATH_LABELS } from '@/utils/labels'
import type { PipelineStage, SourceItem } from '@/types/api'

const props = defineProps<{
  pipeline: PipelineStage[]
  query: string
  sources?: SourceItem[]
}>()

// 六路固定顺序与元数据（金棕单色 SVG 线稿图标用 emoji/字符占位，
// 如需精致图标后续替换为内联 SVG）
const PATH_ORDER = ['semantic', 'question', 'bm25', 'graph', 'entity', 'clip'] as const
const PATH_ICONS: Record<string, string> = {
  semantic: '印', question: '问', bm25: '简',
  graph: '网', entity: '铭', clip: '影',
}

const expanded = ref<string | null>(null)
function togglePath(p: string) {
  expanded.value = expanded.value === p ? null : p
}

const rewriteText = computed(() => props.pipeline.find(s => s.stage === 'rewrite')?.rewrittenQuery || '')
const fuseStage = computed(() => props.pipeline.find(s => s.stage === 'fuse'))
const genStarted = computed(() => props.pipeline.some(s => s.stage === 'generate'))
// 步骤卡移除后并入的 stage：分解 / 专家执行 / 拒答
const decomposeStage = computed(() => props.pipeline.find(s => s.stage === 'decompose'))
const expertStages = computed(() => props.pipeline.filter(s => s.stage === 'expert'))
const refuseStage = computed(() => props.pipeline.find(s => s.stage === 'refuse'))

// 专家角色 → 中文名/图标（后端 AgentRole 枚举值）
const EXPERT_NAMES: Record<string, string> = {
  coordinator: '协调', historian: '史官', craftsman: '工艺师',
  relator: '关联师', synthesizer: '著述',
}
const EXPERT_ICONS: Record<string, string> = {
  coordinator: '协', historian: '史', craftsman: '工',
  relator: '联', synthesizer: '著',
}
function expertName(agent?: string): string {
  return agent ? EXPERT_NAMES[agent] || agent : '专家'
}
function expertIcon(agent?: string): string {
  return agent ? EXPERT_ICONS[agent] || '•' : '•'
}

// 各路 path_done 事件 → computed 映射（一次 find,模板免重复调用）
const pathStageMap = computed(() => {
  const m: Record<string, PipelineStage> = {}
  for (const s of props.pipeline) {
    if (s.stage === 'path_done' && s.name) m[s.name] = s
  }
  return m
})
function pathStage(p: string): PipelineStage | undefined {
  return pathStageMap.value[p]
}
// 该路命中的来源（sources 条目的 paths 包含此路）
function pathSources(p: string): SourceItem[] {
  const list = props.sources || []
  return list.filter(s => (s.paths || []).includes(p)).slice(0, 5)
}
// 检索窗口：收到 rewrite/首个 path_done 即开始，收到 fuse 即结束
const isRetrieving = computed(
  () =>
    props.pipeline.some(s => s.stage === 'rewrite' || s.stage === 'path_done') &&
    !props.pipeline.some(s => s.stage === 'fuse'),
)
// 六路串行执行、事件按序到达 → 第一个未 done 的路即当前运行路
const runningPath = computed(() =>
  isRetrieving.value ? PATH_ORDER.find(p => !pathStage(p)) ?? null : null,
)
function pathClass(p: string): Record<string, boolean> {
  const st = pathStage(p)
  return { running: runningPath.value === p, done: !!st, empty: st?.hits === 0 }
}
const genClass = computed<Record<string, boolean>>(() => ({ done: genStarted.value }))

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}
</script>

<style scoped lang="less">
.pipeline-panel {
  padding: 8px 10px;
  font-size: 12px;
  color: var(--color-primary);
}

.pp-row {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px; margin-bottom: 4px;
  border: 1px solid rgba(var(--color-gold-rgb), 0.25);
  border-radius: 6px;
  background: rgba(253, 250, 243, 0.6);
  transition: border-color 0.2s;
}

.pp-query, .pp-rewrite {
  flex-wrap: wrap;
  .pp-label { font-weight: 600; color: var(--color-gold); }
  .pp-text { flex: 1; min-width: 0; word-break: break-all; color: #6b5a3f; }
}

.pp-path { cursor: pointer; position: relative; }
.pp-icon { width: 18px; text-align: center; color: var(--color-gold); }
.pp-name { flex: 1; font-weight: 500; }
.pp-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: #c8b89a;
  &.running { background: var(--color-gold); animation: pp-pulse 0.8s infinite; }
  &.done { background: #6a9a5c; }
  &.failed { background: var(--color-accent); }
}
.pp-nums { font-size: 11px; color: #8a7a5f; white-space: nowrap; }
.pp-chevron { color: var(--color-ink-faint); font-size: 10px; }

// 步骤卡并入区：分解 / 专家执行 / 拒答
.pp-decompose { flex-wrap: wrap; }
// 子查询列表：后端发完整数据，展示截断在前端做（单行省略，自适应宽度）
.pp-decompose-list {
  display: inline-block;
  max-width: 240px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}
.pp-expert-title {
  font-size: 11px; font-weight: 600; color: var(--color-primary);
  margin: 6px 2px 4px;
}
.pp-expert { opacity: 1; }
.pp-expert.failed { border-color: rgba(196, 30, 58, 0.3); }
.pp-refuse { border-color: rgba(196, 30, 58, 0.35); background: rgba(196, 30, 58, 0.04); }
.pp-refuse .pp-name { color: var(--color-accent); font-weight: 600; }
.pp-refuse .pp-nums { color: var(--color-accent); }

.pp-row.done { border-color: rgba(106, 154, 92, 0.4); }
.pp-row.empty { opacity: 0.55; }

.pp-detail {
  position: absolute; top: 100%; left: 0; right: 0; z-index: 10;
  background: #fffdf7; border: 1px solid rgba(var(--color-gold-rgb), 0.3);
  border-radius: 6px; padding: 6px 8px; margin-top: 2px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}
.pp-detail-line { font-size: 11px; color: #6b5a3f; padding: 2px 0; }
.pp-detail-srcs { margin-top: 4px; border-top: 1px dashed rgba(var(--color-gold-rgb), 0.3); padding-top: 4px; }
.pp-detail-src { padding: 2px 0; }
.pp-detail-srcname { font-weight: 600; color: var(--color-primary); margin-right: 6px; }
.pp-detail-srccontent { color: #8a7a5f; }

.pp-bar {
  display: inline-block; width: 60px; height: 6px;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--color-gold), #e8d9b8);
  animation: pp-grow 1.2s ease-in-out infinite;
}

@keyframes pp-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
@keyframes pp-grow {
  0% { width: 20%; }
  100% { width: 90%; }
}
</style>
