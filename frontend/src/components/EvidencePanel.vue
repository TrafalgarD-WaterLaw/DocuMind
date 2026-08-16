<template>
  <div ref="panelEl" class="evidence-panel">
    <!-- Empty state -->
    <div v-if="!hasData" class="empty-state">
      <el-icon :size="36" color="#c9a96e"><Connection /></el-icon>
      <p>发送问题后<br/>证据链将在此展示</p>
    </div>

    <template v-else>
      <!-- ① 检索路径分布 -->
      <div class="section">
        <h4>
          <el-icon :size="14"><Share /></el-icon>
          检索路径分布
        </h4>
        <div class="path-badges">
          <span
            v-for="p in pathStats"
            :key="p.key"
            class="path-badge"
            :class="p.key"
          >
            <span class="badge-name">{{ p.label }}</span>
            <span class="badge-count">×{{ p.count }}</span>
          </span>
        </div>
        <p v-if="!pathStats.length" class="section-tip">本次回答未标注路径来源</p>
      </div>

      <!-- ② 图谱锚定子图（ECharts 力导向） -->
      <div class="section">
        <h4>
          <el-icon :size="14"><Share /></el-icon>
          图谱锚定
          <span v-if="hasSubgraph" class="graph-meta">{{ graphNodes.length }} 节点 · {{ graphLinks.length }} 关系</span>
        </h4>
        <!-- v-show 而非 v-if：容器常驻，chart 实例不销毁。
             若用 v-if，切到无子图对话时容器销毁、chart 绑定旧 DOM，
             切回来容器重建后 setOption 失效 → 永久空白 -->
        <div v-show="hasSubgraph" ref="graphEl" class="graph-canvas" />
        <div v-if="!hasSubgraph" class="graph-empty">
          {{ hasAnchorData ? '仅文本检索，无图谱锚定数据' : '本次回答未命中知识图谱' }}
        </div>
      </div>

      <!-- ③ 证据来源列表 -->
      <div class="section">
        <h4>
          <el-icon :size="14"><Document /></el-icon>
          证据来源（{{ sources.length }}）
        </h4>
        <div
          v-for="(s, i) in sources"
          :key="i"
          class="source-item"
          :data-index="s.index || i + 1"
          :class="{ highlight: highlightIdx === (s.index || i + 1) }"
        >
          <span class="src-index">{{ s.index || i + 1 }}</span>
          <!-- 图-文并排：图片列（左，最多 3 张竖排）+ 文本列（右） -->
          <div class="src-body">
            <div v-if="s.images?.length || s.image_url" class="src-figure">
              <img
                v-for="(img, k) in (s.images || []).slice(0, 3)"
                :key="k"
                :src="imageAbsUrl(img)"
                class="src-image"
                :class="{ 'img-error': failedImages.has(img) }"
                loading="lazy"
                @error="failedImages.add(img)"
                @click="openImage(img)"
              />
              <img
                v-if="!s.images?.length && s.image_url"
                :src="imageAbsUrl(s.image_url)"
                class="src-image"
                :class="{ 'img-error': failedImages.has(s.image_url) }"
                loading="lazy"
                @error="failedImages.add(s.image_url)"
                @click="openImage(s.image_url)"
              />
            </div>
            <div class="src-text">
              <div class="src-name">
                {{ s.source }}
                <span v-if="s.graph_anchor" class="anchor-tag" title="图谱锚定">图谱</span>
              </div>
              <div v-if="s.paths && s.paths.length" class="src-paths">
                <span v-for="p in s.paths" :key="p" class="path-tag" :class="p">
                  {{ pathLabel(p) }}
                </span>
              </div>
              <div class="src-content">{{ s.content }}</div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

// 按需引入 echarts（全量 import 达 1MB+，拖慢首屏）
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { SourceItem } from '@/types/api'
import { imageAbsUrl } from '@/utils/images'
import { useChatStore } from '@/stores/chat'
import { PATH_LABELS, REL_LABELS } from '@/utils/labels'

echarts.use([GraphChart, TooltipComponent, CanvasRenderer])

function pathLabel(p: string): string {
  return PATH_LABELS[p] || p
}

/** 点击放大：新窗口打开原图（简单可靠，避免引入图片预览组件依赖） */
function openImage(rel: string) {
  window.open(imageAbsUrl(rel), '_blank')
}

// 单轮数据驱动（轮次由 SidePanel 管理，切轮次时 props 变化）
const props = defineProps<{
  round?: import('@/types/domain').ChatMessage | null
}>()

const sources = computed<SourceItem[]>(() => props.round?.sources || [])

// ── 引用聚焦（🟢）：点击回答正文 [N] → 滚动定位 + 高亮对应证据卡 ──
const chatStore = useChatStore()
const panelEl = ref<HTMLDivElement>()
const highlightIdx = ref<number | null>(null)
let highlightTimer: number | undefined

watch(
  () => chatStore.activeCitation,
  async (c) => {
    if (!c || props.round?.id !== c.msgId) return
    highlightIdx.value = c.index
    await nextTick()
    panelEl.value
      ?.querySelector(`.source-item[data-index="${c.index}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    // 2.4s 后熄灭高亮；重复点击同一编号重置计时
    window.clearTimeout(highlightTimer)
    highlightTimer = window.setTimeout(() => {
      if (highlightIdx.value === c.index) highlightIdx.value = null
    }, 2400)
  },
)

// L2: 图片加载失败兜底——文档删除后历史会话的图片 URL 404，隐藏不显示破碎图
const failedImages = ref(new Set<string>())

const hasData = computed(() => !!props.round && sources.value.length > 0)

// 检索路径分布统计
const pathStats = computed(() => {
  const counts: Record<string, number> = {}
  for (const s of sources.value) {
    for (const p of s.paths || []) {
      counts[p] = (counts[p] || 0) + 1
    }
  }
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([key, count]) => ({ key, label: pathLabel(key), count }))
})

// 图谱锚定子图数据（过滤占位符节点，如 EXCAVATED_AT 的空 target "-"）
const graphNodes = computed(() => {
  const anchor = sources.value.find(s => s.graph_anchor)?.graph_anchor
  if (!anchor) return []
  const nodes = new Set<string>([anchor.entity])
  for (const r of anchor.related || []) {
    if (r && r !== '-') nodes.add(r)
  }
  return [...nodes]
})

const graphLinks = computed(() => {
  const anchor = sources.value.find(s => s.graph_anchor)?.graph_anchor
  if (!anchor?.links?.length) return []
  return anchor.links
    // 过滤空/占位 target 与自环
    .filter(l => l.source && l.target && l.target !== '-' && l.source !== l.target)
    // 只保留两端都在节点集合内的边（避免悬空）
    .filter(l => graphNodes.value.includes(l.source) && graphNodes.value.includes(l.target))
    .map(l => ({
      source: l.source,
      target: l.target,
      name: REL_LABELS[l.name] || l.name || '关联',
    }))
})

// 是否显示子图区
const hasSubgraph = computed(() => graphNodes.value.length > 1)

// 是否有图谱锚定数据（区分"无数据"与"渲染中"）
const hasAnchorData = computed(
  () => sources.value.some(s => s.graph_anchor),
)

// ECharts 力导向图
const graphEl = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const anchorEntity = computed(
  () => sources.value.find(s => s.graph_anchor)?.graph_anchor?.entity || '',
)

function renderGraph() {
  // 容器不可见（tab 未激活 / display:none）时跳过——等可见后再渲染
  if (!graphEl.value || !graphEl.value.clientWidth || graphNodes.value.length < 2) return
  if (!chart) {
    chart = echarts.init(graphEl.value)
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(graphEl.value)
  }
  const center = anchorEntity.value
  chart.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        if (params.dataType === 'edge') {
          return `${params.data.source} —[${params.data.name}]→ ${params.data.target}`
        }
        return params.name
      },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      force: { repulsion: 120, edgeLength: [40, 80], gravity: 0.08 },
      data: graphNodes.value.map(n => ({
        name: n,
        symbolSize: n === center ? 28 : 16,
        itemStyle: {
          color: n === center ? '#c9a96e' : '#8a6d3b',
          borderColor: n === center ? '#a8864a' : 'rgba(138,109,59,0.4)',
          borderWidth: n === center ? 2 : 1,
        },
        label: { show: true, fontSize: 10, color: '#5c4a2e', offset: [0, -10] },
      })),
      links: graphLinks.value,
      lineStyle: { color: 'rgba(160,130,70,0.85)', width: 2, curveness: 0.08 },
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: [0, 8],
      // 边标签：显示中文关系（属于/出土于）——用字符串模板，避免函数 formatter 兼容问题
      edgeLabel: {
        show: true,
        fontSize: 9,
        color: '#8a6d3b',
        formatter: '{b}',
      },
      emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
    }],
  })
}

function tryRender() {
  nextTick(() => {
    // tab 隐藏时容器宽为 0，延迟到可见后再渲染
    if (!graphEl.value || !graphEl.value.clientWidth) return
    renderGraph()
  })
}

watch(graphNodes, tryRender)
// 轮次切换（props.round 变化）→ 重新渲染图谱
watch(() => props.round, () => nextTick(renderGraph))

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped lang="less">
.evidence-panel {
  height: 100%;
  overflow-y: auto;
  padding: 12px;
  background: var(--color-card);
}

.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  height: 100%; color: var(--color-ink-faint); font-size: 13px; text-align: center; line-height: 1.8;
  gap: 8px;
}

.section {
  margin-bottom: 18px;

  h4 {
    display: flex; align-items: center; gap: 6px;
    margin: 0 0 10px; font-size: 13px; font-weight: 600;
    color: var(--color-ink);
  }
}

.section-tip { font-size: 12px; color: var(--color-ink-faint); margin: 0; }

// 路径分布徽章
.path-badges {
  display: flex; flex-wrap: wrap; gap: 6px;
}

.path-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 10px;
  font-size: 11px; border: 1px solid;

  .badge-count { font-weight: 700; }

  &.semantic { color: #2f6fb3; border-color: rgba(47,111,179,.3); background: rgba(47,111,179,.06); }
  &.question { color: #7a5cbf; border-color: rgba(122,92,191,.3); background: rgba(122,92,191,.06); }
  &.bm25 { color: #2e8b57; border-color: rgba(46,139,87,.3); background: rgba(46,139,87,.06); }
  &.graph { color: #c0392b; border-color: rgba(192,57,43,.3); background: rgba(192,57,43,.06); }
  &.entity { color: #b8860b; border-color: rgba(184,134,11,.3); background: rgba(184,134,11,.06); }
}

// 图谱子图
.graph-canvas {
  width: 100%; height: 220px;
  border: 1px solid rgba(var(--color-gold-rgb), .25);
  border-radius: 8px; background: rgba(253,250,243,.6);
}

.graph-empty {
  height: 60px;
  display: flex; align-items: center; justify-content: center;
  border: 1px dashed rgba(var(--color-gold-rgb), .3);
  border-radius: 8px;
  font-size: 12px; color: var(--color-ink-faint);
}

.graph-meta {
  font-size: 11px; font-weight: 400; color: var(--color-ink-faint); margin-left: auto;
}

// 证据来源
.source-item {
  display: flex; gap: 8px;
  padding: 8px; margin-bottom: 8px;
  border: 1px solid rgba(var(--color-gold-rgb), .25);
  border-radius: 8px; background: rgba(253,250,243,.7);
  transition: border-color 0.3s, box-shadow 0.3s;

  // 引用聚焦高亮（点击回答正文 [N] 触发，2.4s 后熄灭）
  &.highlight {
    border-color: var(--color-accent);
    box-shadow: 0 0 0 2px rgba(196, 30, 58, 0.18);
  }
}

.src-index {
  flex-shrink: 0;
  width: 20px; height: 20px; border-radius: 50%;
  background: rgba(var(--color-gold-rgb), .18);
  color: var(--color-primary);
  font-size: 11px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}

// 图-文并排（🟡）：图片列在左、文本列在右
.src-body {
  display: flex; gap: 8px;
  min-width: 0; flex: 1;
}

.src-figure {
  flex-shrink: 0;
  width: 84px;
  display: flex; flex-direction: column; gap: 4px;
}

.src-text { flex: 1; min-width: 0; }

.src-image {
  display: block;
  width: 100%;
  max-height: 66px;
  object-fit: cover;
  &.img-error {
    display: none;
  }
  border: 1px solid rgba(var(--color-gold-rgb), 0.35);
  border-radius: 6px;
  cursor: zoom-in;
  transition: opacity 0.2s;
  &:hover { opacity: 0.85; }
}

.src-name {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 600; color: var(--color-ink);
  word-break: break-all;
}

.anchor-tag {
  flex-shrink: 0;
  font-size: 10px; font-weight: 500; color: #c0392b;
  border: 1px solid rgba(192,57,43,.3); border-radius: 4px;
  padding: 0 4px; background: rgba(192,57,43,.05);
}

.src-paths { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }

.path-tag {
  font-size: 10px; padding: 0 5px; border-radius: 4px;
  color: #8a6d3b; background: rgba(var(--color-gold-rgb), .12);
  border: 1px solid rgba(var(--color-gold-rgb), .25);
}

.src-content {
  margin-top: 4px;
  font-size: 11px; line-height: 1.6; color: #6d5c40;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
