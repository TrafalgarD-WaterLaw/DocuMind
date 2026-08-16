import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { GraphNodeData, GraphLinkData } from '@/types/api'
import { initGraph, expandNode as apiExpandNode, searchGraph as apiSearchGraph } from '@/api/knowledge'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const nodes = ref<GraphNodeData[]>([])
  const links = ref<GraphLinkData[]>([])
  const expandedNodes = ref<Set<string>>(new Set())
  /** 展开记录：节点名 → 其展开时引入的子节点名（用于收起） */
  const expandMap = ref<Record<string, string[]>>({})
  const loading = ref(false)

  async function fetchInitGraph() {
    loading.value = true
    try {
      const data = await initGraph()
      
      nodes.value = data.echarts_data
      links.value = data.nodes_relation
      expandedNodes.value = new Set()
      expandMap.value = {}
    } finally {
      loading.value = false
    }
  }

  async function fetchExpandNode(nodeName: string) {
    if (expandedNodes.value.has(nodeName)) return
    loading.value = true
    try {
      const before = new Set(nodes.value.map(n => n.name))
      const data = await apiExpandNode({
        node_data: nodes.value,
        link_data: links.value,
        node_name: nodeName,
        cypher_query: '',
      })
      nodes.value = data.echarts_data
      links.value = data.nodes_relation
      // 记录本次展开引入的节点（收起时按此移除）
      const added = nodes.value
        .filter(n => !before.has(n.name))
        .map(n => n.name)
      // 无新增（关联都已显示）则不标记为已展开，避免出现无效金边
      if (added.length === 0) return
      expandMap.value[nodeName] = added
      expandedNodes.value.add(nodeName)
    } finally {
      loading.value = false
    }
  }

  /** 收起节点：移除其展开引入的子节点与边（子节点若也展开则递归收起） */
  function collapseNode(nodeName: string) {
    const added = expandMap.value[nodeName] || []
    const toRemove = new Set(added)
    // 递归收起已展开的子节点
    for (const a of added) {
      if (expandedNodes.value.has(a)) {
        ;(expandMap.value[a] || []).forEach(s => toRemove.add(s))
        delete expandMap.value[a]
        expandedNodes.value.delete(a)
      }
    }
    nodes.value = nodes.value.filter(n => !toRemove.has(n.name))
    links.value = links.value.filter(
      l => !toRemove.has(l.source) && !toRemove.has(l.target),
    )
    delete expandMap.value[nodeName]
    expandedNodes.value.delete(nodeName)
  }

  async function fetchSearchGraph(query: string) {
    loading.value = true
    try {
      const data = await apiSearchGraph({
        node_data: [],
        link_data: [],
        node_name: '',
        cypher_query: query,
      })
      nodes.value = data.echarts_data
      links.value = data.nodes_relation
      // 搜索替换整个图谱，展开状态一并重置
      expandedNodes.value = new Set()
      expandMap.value = {}
    } finally {
      loading.value = false
    }
  }

  function clearGraph() {
    nodes.value = []
    links.value = []
    expandedNodes.value = new Set()
  }

  return {
    nodes, links, expandedNodes, expandMap, loading,
    initGraph: fetchInitGraph,
    expandNode: fetchExpandNode,
    collapseNode,
    searchGraph: fetchSearchGraph,
    clearGraph,
  }
})
