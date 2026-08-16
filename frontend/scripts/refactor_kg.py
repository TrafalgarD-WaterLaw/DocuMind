# -*- coding: utf-8 -*-
"""一次性重构脚本:KnowledgeGraphPanel 拆分(执行后可删)"""
import re
from pathlib import Path

p = Path("src/components/KnowledgeGraphPanel.vue")
t = p.read_text(encoding="utf-8")
orig_len = len(t)

# 1. 模板:详情面板 aside 整块 → 子组件
t = re.sub(
    r'      <!-- ═══ 详情面板：图鉴统计 / 选中节点信息 ═══ -->\n      <aside class="kg-detail">.*?</aside>\n',
    '      <!-- ═══ 详情面板（KgDetailPanel 子组件）：图鉴统计 / 选中节点信息 ═══ -->\n'
    '      <KgDetailPanel\n'
    '        :selected-node="selectedNode"\n'
    '        :stats="stats"\n'
    '        :node-relations="nodeRelations"\n'
    '        :is-expanded="isExpanded"\n'
    '        :cat-color="catColor"\n'
    '        :category-label="categoryLabel"\n'
    '        @select="selectNode"\n'
    '        @expand="toggleExpandNode"\n'
    '      />\n',
    t,
    flags=re.S,
)

# 2. import KgDetailPanel
t = t.replace(
    "import { useKnowledgeStore } from '@/stores/knowledge'",
    "import KgDetailPanel from '@/components/KgDetailPanel.vue'\n"
    "import { useKnowledgeStore } from '@/stores/knowledge'",
)

# 3. handle* → on*（模板与 script 同步）
t = t.replace("handleSearch", "onSearch").replace("handleReset", "onReset")

# 4. updateChart(notMerge) → rebuildChart / mergeChart
t = t.replace("updateChart(true)", "rebuildChart()").replace("updateChart(false)", "mergeChart()")
old_update = '''/**
 * 刷新图表
 * @param notMerge true=全量重建（筛选/聚焦/初始化）；false=增量合并（展开/收起，保持已有节点稳定）
 */
function updateChart(notMerge = false) {
  if (!chartInstance) return
  chartInstance.setOption(buildChartOptions(), notMerge)
}'''
new_update = '''/** 全量重建（筛选/聚焦/初始化）——setOption 第二参 notMerge=true 不合并旧配置 */
function rebuildChart() {
  if (!chartInstance) return
  chartInstance.setOption(buildChartOptions(), true)
}

/** 增量合并（展开/收起，保持已有节点稳定不散架） */
function mergeChart() {
  if (!chartInstance) return
  chartInstance.setOption(buildChartOptions(), false)
}'''
t = t.replace(old_update, new_update)

# 5. 展开/收起重复合并
old_toggle = '''async function handleNodeClick(name: string) {
  const node = store.nodes.find(n => n.name === name)
  if (node) selectedNode.value = node
  // 展开 / 收起切换
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

async function toggleExpandNode() {
  const name = selectedNode.value?.name
  if (!name) return
  if (store.expandedNodes.has(name)) {
    store.collapseNode(name)
  } else {
    try {
      await store.expandNode(name)
    } catch {
      errorMsg.value = '节点展开失败，请稍后重试'
    }
  }
}'''
new_toggle = '''async function toggleNodeExpand(name: string) {
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
}'''
t = t.replace(old_toggle, new_toggle)

# handleNodeClick 在 initChart 的 chartInstance.on('click') 里也被引用
t = t.replace("handleNodeClick(p.data.name)", "onNodeClick(p.data.name)")

# 6. search 双 find 合并
t = t.replace(
    '''    // 自动选中命中节点
    const hit = store.nodes.find(n => n.name === q)
      || store.nodes.find(n => n.name.includes(q))
    if (hit) selectedNode.value = hit''',
    '''    // 自动选中命中节点（精确匹配自然被 includes 覆盖，一次 find 即可）
    const hit = store.nodes.find(n => n.name.includes(q))
    if (hit) selectedNode.value = hit''',
)

# 7. (s as any).graph 类型化
t = t.replace(
    '''    .then(s => {
      const g = (s as any).graph
      if (g) {''',
    '''    .then(s => {
      const g = s.graph
      if (g) {''',
)

# 8. 删除已移入 KgDetailPanel 的样式块（.kg-detail 到 .btn-expand 结束）
t = re.sub(r'\.kg-detail \{.*?\.btn-expand \{\s*[^}]*\}\s*\}\s*', '', t, flags=re.S)

p.write_text(t, encoding="utf-8")
print(f"改写完成:{orig_len} → {len(t)} 字符")
