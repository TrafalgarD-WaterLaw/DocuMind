<template>
  <div class="kg-panel">
    <!-- ═══ 题头 ═══ -->
    <header class="kg-header">
      <div class="kg-heading">
        <h2 class="kg-title">知识图谱</h2>
        <p class="kg-subtitle">文物关系网络 · 点击节点展开 · 拖动平移 · 滚轮缩放</p>
      </div>
      <div class="kg-search">
        <el-input
          v-model="searchQuery"
          placeholder="搜索文物、朝代、窑口…"
          class="search-input"
          clearable
          @keyup.enter="onSearch"
        />
        <button class="btn-search" @click="onSearch">搜索</button>
        <button class="btn-plain" @click="onReset">重置</button>
      </div>
    </header>

    <!-- ═══ 朝代时间轴：按真实时序排列，点击聚焦该朝代 ═══ -->
    <div class="era-strip">
      <span class="era-label">朝代</span>
      <div class="era-track">
        <button
          v-for="era in eras"
          :key="era"
          class="era-chip"
          :class="{ active: activeEra === era }"
          :title="`聚焦${era}的文物网络`"
          @click="toggleEra(era)"
        >
          <span class="era-dot" />
          <span class="era-name">{{ era }}</span>
        </button>
      </div>
    </div>

    <!-- ═══ 主体：画布 + 详情面板 ═══ -->
    <div class="kg-body">
      <section class="kg-canvas-wrap">
        <div ref="chartRef" class="chart-container" />

        <!-- 覆盖层：加载 / 错误 / 空结果 / 全隐藏 -->
        <div v-if="overlay" class="chart-overlay">
          <template v-if="overlay.type === 'loading'">
            <el-icon class="is-loading" :size="28"><Loading /></el-icon>
            <p class="overlay-text">图谱加载中…</p>
          </template>
          <template v-else-if="overlay.type === 'error'">
            <p class="overlay-text">{{ overlay.msg }}</p>
            <button class="btn-plain" @click="onReset">重试</button>
          </template>
          <template v-else-if="overlay.type === 'empty'">
            <p class="overlay-text">未找到「{{ lastSearch || '该关键词' }}」相关文物</p>
            <button class="btn-plain" @click="onReset">回到全景</button>
          </template>
          <template v-else>
            <p class="overlay-text">当前筛选下没有可见节点</p>
            <button class="btn-plain" @click="restoreAll">恢复全部</button>
          </template>
        </div>

        <!-- 图例：点击开关类别可见性 -->
        <div class="kg-legend">
          <button
            v-for="c in CATS"
            :key="c.key"
            class="legend-chip"
            :class="{ off: hiddenCats.has(c.key) }"
            :title="`显示 / 隐藏${c.label}`"
            @click="toggleCat(c.key)"
          >
            <span class="legend-symbol" :class="'shape-' + c.shape" :style="symbolStyle(c)" />
            {{ c.label }}
          </button>
          <span class="legend-note">金边 = 已展开</span>
        </div>

        <!-- 缩放控制 -->
        <div class="kg-zoom">
          <button class="zoom-btn" title="放大" @click="zoomBy(ZOOM_IN_FACTOR)">＋</button>
          <button class="zoom-btn" title="缩小" @click="zoomBy(ZOOM_OUT_FACTOR)">－</button>
          <button class="zoom-btn" title="复位视图" @click="resetView">复位</button>
        </div>
      </section>

      <!-- ═══ 详情面板（KgDetailPanel 子组件）：图鉴统计 / 选中节点信息 ═══ -->
      <KgDetailPanel
        :selected-node="selectedNode"
        :stats="stats"
        :node-relations="nodeRelations"
        :is-expanded="isExpanded"
        :cat-color="catColor"
        :category-label="categoryLabel"
        @select="selectNode"
        @expand="toggleExpandNode"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { REL_LABELS } from '@/utils/labels'
import * as echarts from 'echarts'
import { Loading } from '@element-plus/icons-vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import KgDetailPanel from '@/components/KgDetailPanel.vue'
import { fetchStats } from '@/api/stats'
import type { GraphNodeData } from '@/types/api'
import type { GraphNode, GraphLink } from '@/types/graph'

const store = useKnowledgeStore()

// ── 类别元信息：文物调色板（朱砂 / 青瓷 / 黛蓝 / 赭褐 + 鎏金强调）──
const CATS = [
  { key: 'Era', label: '朝代', color: '#c41e3a', shape: 'circle' },
  { key: 'Kiln', label: '窑口', color: '#3f7a6a', shape: 'rect' },
  { key: 'Site', label: '遗址', color: '#4a6b8c', shape: 'triangle' },
  { key: 'Artifact', label: '器物', color: '#96703f', shape: 'dot' },
] as const
type CatKey = (typeof CATS)[number]['key']
const GOLD = '#c9a96e'
const ZOOM_IN_FACTOR = 1.25   // 图谱缩放步进
const ZOOM_OUT_FACTOR = 0.8

const hiddenCats = ref<Set<CatKey>>(new Set())
const activeEra = ref<string | null>(null)
const searchQuery = ref('')
const lastSearch = ref('')
const selectedNode = ref<GraphNodeData | null>(null)
const errorMsg = ref('')
const stats = ref<{ artifacts: number; sites: number; eras: number; kilns: number } | null>(null)

const chartRef = ref<HTMLElement | null>(null)
let chartInstance: echarts.ECharts | null = null
/** 节点位置快照：'finished' 时记录，重建时回填为初始坐标 → 筛选 / 聚焦不散架 */
const posSnapshot = new Map<string, { x: number; y: number }>()

// ── 朝代时序（时间轴排序依据，真实年代顺序）──
const ERA_ORDER = [
  '新石器时代', '夏代', '商代', '西周', '春秋', '战国', '秦代', '汉代', '魏晋',
  '南北朝', '隋代', '唐代', '宋代', '元代', '明代', '清代', '民国', '近现代',
]
function eraRank(name: string): number {
  const i = ERA_ORDER.indexOf(name)
  return i === -1 ? 999 : i
}

// ── 类别归一（兼容后端 "Artifact, Era" 多标签）──
function normCat(cat?: string): string {
  const c = (cat || 'Unknown').split(',')[0].trim()
  return CATS.some(x => x.key === c) ? c : 'Unknown'
}
function catColor(cat?: string): string {
  const c = normCat(cat)
  return CATS.find(x => x.key === c)?.color ?? '#8a8575'
}
const CAT_LABELS: Record<string, string> = {
  Era: '朝代', Kiln: '窑口', Site: '遗址', Artifact: '器物', Unknown: '未知',
}
function categoryLabel(cat?: string): string {
  return CAT_LABELS[normCat(cat)] || cat || '未知'
}

function relLabel(name?: string): string {
  return REL_LABELS[name || ''] || name || '关联'
}

// ── 朝代时间轴 ──
const eras = computed(() => Array.from(
  new Set(store.nodes.filter(n => normCat(n.category) === 'Era').map(n => n.name)),
).sort((a, b) => eraRank(a) - eraRank(b)))

function toggleEra(era: string) {
  activeEra.value = activeEra.value === era ? null : era
  rebuildChart()
}

// ── 可见性：类别开关 + 时代聚焦（朝代 + 其器物 + 器物关联的窑口/遗址，2 跳）──
const eraNames = computed<Set<string> | null>(() => {
  const era = activeEra.value
  if (!era) return null
  const names = new Set<string>([era])
  const artifacts = store.links
    .filter(l => l.name === 'BELONGS_TO' && l.target === era)
    .map(l => l.source)
  artifacts.forEach(n => names.add(n))
  for (const l of store.links) {
    if ((l.name === 'BELONGS_TO_KILN' || l.name === 'EXCAVATED_AT')
      && artifacts.includes(l.source)) {
      names.add(l.target)
    }
  }
  return names
})

const visibleNodes = computed(() => {
  let pool = store.nodes
  if (hiddenCats.value.size) {
    pool = pool.filter(n => !hiddenCats.value.has(normCat(n.category) as CatKey))
  }
  if (eraNames.value) {
    pool = pool.filter(n => eraNames.value!.has(n.name))
  }
  return pool
})

const visibleLinks = computed(() => {
  const s = new Set(visibleNodes.value.map(n => n.name))
  return store.links.filter(l => s.has(l.source) && s.has(l.target))
})

/** 过滤 / 聚焦模式下器物标签也常显（全图时仅 hover 显示，避免杂乱） */
const showAllLabels = computed(() => hiddenCats.value.size > 0 || !!activeEra.value)

// ── 覆盖层状态 ──
const overlay = computed(() => {
  if (store.loading) return { type: 'loading' }
  if (errorMsg.value) return { type: 'error', msg: errorMsg.value }
  if (store.nodes.length === 0) return { type: 'empty' }
  if (visibleNodes.value.length === 0) return { type: 'hidden' }
  return null
})

// ── 图表构建 ──
/** 图例符号内联样式（三角用边框色，其余用背景色） */
function symbolStyle(c: (typeof CATS)[number]) {
  if (c.shape === 'triangle') {
    return {
      borderLeftColor: 'transparent',
      borderRightColor: 'transparent',
      borderBottomColor: c.color,
    }
  }
  return { background: c.color }
}

function nodeSymbol(cat: string): string {
  if (cat === 'Kiln') return 'rect'
  if (cat === 'Site') return 'triangle'
  return 'circle'
}

function nodeSize(cat: string, nameLen: number): number {
  if (cat === 'Era') return 46
  if (cat === 'Kiln') return 34
  if (cat === 'Site') return 28
  return nameLen > 6 ? 20 : 15
}

function buildChartOptions(): echarts.EChartsOption {
  const nodes = visibleNodes.value
  const links = visibleLinks.value
  const isExpanded = (name: string) => store.expandedNodes.has(name)

  const graphNodes: GraphNode[] = nodes.map(node => {
    const cat = normCat(node.category)
    const pos = posSnapshot.get(node.name)
    const exp = isExpanded(node.name)
    return {
      id: node.name,
      name: node.name,
      category: categoryLabel(cat),
      symbol: nodeSymbol(cat),
      symbolSize: nodeSize(cat, node.name.length),
      // 位置快照回填：重建后从原位置继续受力，布局稳定不散架
      x: pos?.x,
      y: pos?.y,
      itemStyle: {
        color: catColor(cat),
        borderWidth: exp ? 3 : 1,
        borderColor: exp ? GOLD : 'rgba(139,69,19,0.12)',
      },
      label: {
        show: showAllLabels.value || cat !== 'Artifact',
        position: 'right',
        fontSize: 11,
        color: '#5c4033',
      },
      introduce: node.introduce,
      when: node.when,
      where: node.where,
    }
  })

  const graphLinks: GraphLink[] = links.map(link => ({
    source: link.source,
    target: link.target,
    // 聚焦时代时显示关系标签（阅读模式），全图默认隐藏避免杂乱
    label: {
      show: !!activeEra.value,
      formatter: relLabel(link.name),
      fontSize: 9,
      color: '#8b7355',
    },
    lineStyle: {
      width: 1.2,
      curveness: 0.3,
      color: 'rgba(201,169,110,0.55)',
    },
  }))

  return {
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params: unknown) => {
        const p = params as { data?: Record<string, any> }
        const d = p?.data
        if (!d?.name) return ''
        const meta = d.when || d.where
          ? `<div style="margin-top:2px;font-size:11px;color:#b8860b;">${d.when || ''}${d.where ? (d.when ? ' · ' : '') + '藏于' + d.where : ''}</div>`
          : ''
        const intro = d.introduce
          ? `<div style="margin-top:4px;font-size:11px;color:#8b7355;line-height:1.6;">${String(d.introduce).slice(0, 40)}${String(d.introduce).length > 40 ? '…' : ''}</div>`
          : ''
        return `<div style="font-size:13px;font-weight:600;color:#8b4513;font-family:serif;">${d.name}</div>` +
          `<div style="font-size:11px;color:#c41e3a;margin-top:1px;">${d.category || ''}</div>${meta}${intro}`
      },
      extraCssText:
        'background:#fdfaf3;border:1px solid #c9a96e;border-radius:8px;' +
        'box-shadow:0 4px 16px rgba(139,69,19,0.18);padding:8px 12px;',
      textStyle: { fontSize: 12, color: '#5c4033' },
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        data: graphNodes,
        links: graphLinks,
        roam: true,
        draggable: true,
        force: {
          repulsion: 250,
          edgeLength: [70, 150],
          gravity: 0.1,
          friction: ZOOM_OUT_FACTOR,
          // 关闭持续动画：节点布局一次性成型，不再漂移
          layoutAnimation: false,
        },
        emphasis: {
          focus: 'adjacency',
          itemStyle: { borderWidth: 3, borderColor: '#8b4513' },
          lineStyle: { width: 3, color: 'rgba(139,69,19,0.7)' },
          label: { show: true, fontWeight: 600 },
        },
        scaleLimit: { min: 0.4, max: 4 },
        label: {
          show: true,
          position: 'right',
          fontSize: 11,
          color: '#5c4033',
        },
      },
    ],
  }
}

// ── 图表生命周期 ──
function initChart() {
  if (!chartRef.value) return
  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
    chartInstance.on('click', (params: unknown) => {
      const p = params as { dataType?: string; data?: { name?: string } }
      if (p?.dataType === 'node' && p?.data?.name) {
        onNodeClick(p.data.name)
      }
    })
    // 布局稳定后快照节点坐标
    chartInstance.on('finished', snapshotPositions)
  }
  rebuildChart()
}

/** 全量重建（筛选/聚焦/初始化）——setOption 第二参 notMerge=true 不合并旧配置 */
function rebuildChart() {
  if (!chartInstance) return
  chartInstance.setOption(buildChartOptions(), true)
}

/** 增量合并（展开/收起，保持已有节点稳定不散架） */
function mergeChart() {
  if (!chartInstance) return
  chartInstance.setOption(buildChartOptions(), false)
}

function snapshotPositions() {
  const model = (chartInstance as any)?.getModel()
  const series = model?.getSeriesByIndex(0) as any
  const data = series?.getGraph?.()?.data
  if (!data) return
  data.each((idx: number) => {
    const name = data.getName(idx)
    if (name == null) return
    const layout = data.getItemLayout(idx)
    if (!layout) return
    const p = Array.isArray(layout)
      ? { x: layout[0], y: layout[1] }
      : { x: layout.x, y: layout.y }
    if (typeof p.x === 'number' && typeof p.y === 'number') {
      posSnapshot.set(name, p)
    }
  })
}

// ── 缩放控制（graphRoam action，zoom 为倍率）──
function currentZoom(): number {
  const cs = ((chartInstance as any)?.getModel().getSeriesByIndex(0) as any)?.coordinateSystem
  return cs?.getZoom?.() ?? 1
}

function zoomBy(factor: number) {
  const rect = chartRef.value?.getBoundingClientRect()
  chartInstance?.dispatchAction({
    type: 'graphRoam',
    zoom: factor,
    originX: rect ? rect.width / 2 : 0,
    originY: rect ? rect.height / 2 : 0,
  } as any)
}

function resetView() {
  const cz = currentZoom()
  if (Math.abs(cz - 1) < 0.001) return
  const rect = chartRef.value?.getBoundingClientRect()
  chartInstance?.dispatchAction({
    type: 'graphRoam',
    zoom: 1 / cz,
    originX: rect ? rect.width / 2 : 0,
    originY: rect ? rect.height / 2 : 0,
  } as any)
}

// ── 交互 ──
async function toggleNodeExpand(name: string) {
  // 展开 / 收起切换（画布点击与详情面板按钮共用）
  if (store.expandedNodes.has(name)) {
    store.collapseNode(name)
  } else {
    try {
      await store.expandNode(name)
    } catch {
      errorMsg.value = '节点展开失败，请稍后重试'
    }
  }
}

async function onNodeClick(name: string) {
  const node = store.nodes.find(n => n.name === name)
  if (node) selectedNode.value = node
  await toggleNodeExpand(name)
}

async function toggleExpandNode() {
  const name = selectedNode.value?.name
  if (!name) return
  await toggleNodeExpand(name)
}

function selectNode(name: string) {
  const n = store.nodes.find(x => x.name === name)
  if (n) selectedNode.value = n
}

function toggleCat(key: CatKey) {
  const next = new Set(hiddenCats.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  hiddenCats.value = next
  rebuildChart()
}

function restoreAll() {
  hiddenCats.value = new Set()
  activeEra.value = null
  rebuildChart()
}

async function onSearch() {
  const q = searchQuery.value.trim()
  if (!q) return
  lastSearch.value = q
  activeEra.value = null
  errorMsg.value = ''
  selectedNode.value = null
  try {
    await store.searchGraph(q)
    // 自动选中命中节点（精确匹配自然被 includes 覆盖，一次 find 即可）
    const hit = store.nodes.find(n => n.name.includes(q))
    if (hit) selectedNode.value = hit
  } catch {
    errorMsg.value = '搜索失败，请检查后端服务'
  }
}

async function onReset() {
  searchQuery.value = ''
  lastSearch.value = ''
  selectedNode.value = null
  activeEra.value = null
  hiddenCats.value = new Set()
  errorMsg.value = ''
  posSnapshot.clear()
  try {
    await store.initGraph()
  } catch {
    errorMsg.value = '图谱加载失败，请检查后端服务'
  }
}

// ── 选中节点的关联关系（与画布可见范围一致）──
const nodeRelations = computed(() => {
  const sel = selectedNode.value
  if (!sel) return []
  return visibleLinks.value
    .filter(l => l.source === sel.name || l.target === sel.name)
    .map(l => ({
      rel: relLabel(l.name),
      other: l.source === sel.name ? l.target : l.source,
      dir: l.source === sel.name ? 'out' as const : 'in' as const,
    }))
})

const isExpanded = computed(
  () => !!selectedNode.value && store.expandedNodes.has(selectedNode.value.name),
)

// ── 数据变化 → 增量合并刷新（展开/收起/搜索）──
watch(
  () => [store.nodes, store.links] as const,
  () => mergeChart(),
  { deep: true },
)

function handleResize() {
  chartInstance?.resize()
}

function onMountedOnce() {
  initChart()
  window.addEventListener('resize', handleResize)
  // 图鉴统计（后端不可用则隐藏统计块）
  fetchStats()
    .then(s => {
      const g = s.graph
      if (g) {
        stats.value = {
          artifacts: g.artifacts ?? 0,
          sites: g.sites ?? 0,
          eras: g.eras ?? 0,
          kilns: g.kilns ?? 0,
        }
      }
    })
    .catch(() => { /* 统计不可用，隐藏图鉴统计 */ })
  // 初始图谱
  store.initGraph().catch(() => {
    errorMsg.value = '图谱加载失败，请检查后端服务'
  })
}

onMounted(onMountedOnce)

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chartInstance?.dispose()
  chartInstance = null
})
</script>

<style scoped lang="less">
.kg-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-paper);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 2px 16px rgba(139, 69, 19, 0.06), 0 4px 32px rgba(139, 69, 19, 0.04);
}

// ═══ 题头 ═══
.kg-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 20px;
  background: var(--color-card);
  border-bottom: 1px solid rgba(var(--color-gold-rgb), 0.35);
  flex-shrink: 0;
  position: relative;

  // 顶部鎏金渐变线
  &::before {
    content: '';
    position: absolute;
    top: 0;
    left: 2%;
    right: 2%;
    height: 2px;
    background: linear-gradient(
      90deg,
      transparent 0%,
      var(--color-gold) 15%,
      var(--color-gold) 85%,
      transparent 100%
    );
    border-radius: 1px;
  }
}

.kg-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-primary);
  letter-spacing: 0.06em;
  margin: 0;
}

.kg-subtitle {
  font-size: 11px;
  color: var(--color-ink-faint);
  margin: 3px 0 0;
  letter-spacing: 0.03em;
}

.kg-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;

  .search-input {
    width: 250px;
  }

  // ElInput 金色描边
  :deep(.el-input) {
    .el-input__wrapper {
      border-radius: 8px;
      border: 1px solid var(--color-gold);
      box-shadow: none;
      background: var(--color-card);
      transition: border-color 0.3s ease, box-shadow 0.3s ease;
      padding-left: 14px;
      padding-right: 14px;

      &:hover { border-color: var(--color-primary); }
      &.is-focus {
        border-color: var(--color-primary);
        box-shadow: 0 0 0 1px rgba(139, 69, 19, 0.2);
      }
    }

    .el-input__inner {
      color: var(--color-ink);
      &::placeholder { color: #b8a99a; font-style: italic; }
    }

    .el-input__suffix { color: var(--color-gold); }
    .el-input__clear {
      color: var(--color-gold);
      &:hover { color: var(--color-primary); }
    }
  }
}

.btn-search {
  padding: 0 18px;
  height: 34px;
  background: var(--color-accent);
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.05em;
  cursor: pointer;
  transition: all 0.25s ease;

  &:hover { box-shadow: 0 2px 8px rgba(196, 30, 58, 0.3); }
}

.btn-plain {
  padding: 0 16px;
  height: 34px;
  background: transparent;
  color: var(--color-primary);
  border: 1px solid var(--color-primary);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.25s ease;

  &:hover { background: var(--color-primary); color: var(--color-card); }
}

// ═══ 朝代时间轴 ═══
.era-strip {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 20px 7px;
  background: var(--color-card);
  border-bottom: 1px solid rgba(var(--color-gold-rgb), 0.35);
  flex-shrink: 0;
}

.era-label {
  font-size: 12px;
  color: var(--color-ink-muted);
  font-weight: 600;
  letter-spacing: 0.12em;
  flex-shrink: 0;
}

.era-track {
  flex: 1;
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding: 3px 2px 5px;

  &::-webkit-scrollbar { height: 4px; }
  &::-webkit-scrollbar-track { background: transparent; }
  &::-webkit-scrollbar-thumb {
    background: rgba(var(--color-gold-rgb), 0.5);
    border-radius: 2px;
  }
}

.era-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 12px;
  border-radius: 12px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  color: #5c4033;
  white-space: nowrap;
  transition: all 0.2s;

  .era-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--color-gold);
    transition: background 0.2s;
  }

  &:hover {
    border-color: var(--color-gold);
    background: rgba(var(--color-gold-rgb), 0.1);
  }

  &.active {
    background: var(--color-primary);
    color: var(--color-card);
    border-color: var(--color-primary);

    .era-dot { background: var(--color-gold); }
  }
}

// ═══ 主体 ═══
.kg-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 12px;
  padding: 12px 16px 16px;
}

// ── 画布 ──
.kg-canvas-wrap {
  flex: 1;
  min-width: 0;
  position: relative;
  border: 1px solid var(--color-gold);
  border-radius: 10px;
  overflow: hidden;
  background:
    radial-gradient(ellipse at 30% 20%, rgba(var(--color-gold-rgb), 0.08), transparent 60%),
    var(--color-card);
  transition: border-color 0.3s ease;

  &:hover { border-color: var(--color-primary); }
}

.chart-container {
  position: absolute;
  inset: 0;
}

// ── 覆盖层 ──
.chart-overlay {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: rgba(253, 250, 243, 0.92);

  .is-loading {
    color: var(--color-gold);
    animation: kg-spin 1.2s linear infinite;
  }
}

.overlay-text {
  color: var(--color-primary);
  font-size: 14px;
  letter-spacing: 0.05em;
  margin: 0;
}

@keyframes kg-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

// ── 图例（分类开关）──
.kg-legend {
  position: absolute;
  left: 12px;
  bottom: 12px;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: rgba(253, 250, 243, 0.95);
  border: 1px solid rgba(var(--color-gold-rgb), 0.45);
  border-radius: 10px;
  box-shadow: 0 2px 10px rgba(139, 69, 19, 0.08);
  user-select: none;
}

.legend-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  font-size: 12px;
  color: #5c4033;
  background: transparent;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover { background: rgba(var(--color-gold-rgb), 0.12); }
  &.off {
    opacity: 0.3;
    text-decoration: line-through;
  }
}

.legend-symbol {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
}

.shape-circle { border-radius: 50%; }
.shape-rect { border-radius: 2px; }
.shape-dot { width: 7px; height: 7px; border-radius: 50%; }
.shape-triangle {
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-bottom: 10px solid;
}

.legend-note {
  font-size: 10px;
  color: var(--color-ink-faint);
  margin-left: 4px;
  padding-left: 8px;
  border-left: 1px solid rgba(var(--color-gold-rgb), 0.4);
  white-space: nowrap;
}

// ── 缩放控制 ──
.kg-zoom {
  position: absolute;
  right: 12px;
  bottom: 12px;
  z-index: 4;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.zoom-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 1px solid rgba(var(--color-gold-rgb), 0.5);
  background: rgba(253, 250, 243, 0.95);
  color: var(--color-primary);
  font-size: 15px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    background: var(--color-primary);
    color: var(--color-card);
    border-color: var(--color-primary);
  }
}


// ═══ 响应式 ═══
@media (max-width: 900px) {
  .kg-search .search-input { width: 160px; }
}
</style>
