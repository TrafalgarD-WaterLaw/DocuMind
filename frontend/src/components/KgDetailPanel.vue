<template>
  <!-- 详情面板：图鉴统计 / 选中节点信息（自 KnowledgeGraphPanel 拆出） -->
  <aside class="kg-detail">
    <div v-if="!selectedNode" class="detail-empty">
      <h3 class="detail-title">文物图鉴</h3>
      <div v-if="stats" class="stat-grid">
        <div class="stat"><b>{{ stats.eras }}</b><span>朝代</span></div>
        <div class="stat"><b>{{ stats.artifacts }}</b><span>器物</span></div>
        <div class="stat"><b>{{ stats.kilns }}</b><span>窑口</span></div>
        <div class="stat"><b>{{ stats.sites }}</b><span>遗址</span></div>
      </div>
      <ul class="guide">
        <li>点击节点：选中并展开其关联</li>
        <li>点击朝代标尺：聚焦一个时代的网络</li>
        <li>拖动 / 滚轮：巡览全图</li>
      </ul>
    </div>

    <div v-else class="detail-body">
      <div class="detail-head">
        <span class="cat-badge" :style="{ background: catColor(selectedNode.category) }">
          {{ categoryLabel(selectedNode.category) }}
        </span>
        <h3 class="detail-title">{{ selectedNode.name }}</h3>
        <span v-if="isExpanded" class="expanded-tag">已展开</span>
      </div>

      <img v-if="selectedNode.image" :src="selectedNode.image" class="detail-image" alt="" />

      <p v-if="selectedNode.when || selectedNode.where" class="detail-meta">
        <span v-if="selectedNode.when">{{ selectedNode.when }}</span>
        <span v-if="selectedNode.where">藏于 {{ selectedNode.where }}</span>
      </p>

      <p v-if="selectedNode.introduce" class="detail-intro">{{ selectedNode.introduce }}</p>
      <p v-else class="detail-intro detail-intro-muted">暂无简介</p>

      <div v-if="nodeRelations.length" class="detail-rels">
        <h4 class="rels-title">关联关系 <span class="rels-count">{{ nodeRelations.length }}</span></h4>
        <ul class="rels-list">
          <li v-for="(r, i) in nodeRelations" :key="i" class="rel-row">
            <span class="rel-name">{{ r.rel }}</span>
            <span class="rel-arrow">{{ r.dir === 'out' ? '→' : '←' }}</span>
            <button class="rel-target" @click="emit('select', r.other)">{{ r.other }}</button>
          </li>
        </ul>
      </div>

      <button class="btn-expand" @click="emit('expand')">
        {{ isExpanded ? '收起该节点' : '展开该节点' }}
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import type { GraphNodeData } from '@/types/api'

defineProps<{
  selectedNode: GraphNodeData | null
  stats: { artifacts: number; sites: number; eras: number; kilns: number } | null
  nodeRelations: { rel: string; other: string; dir: 'out' | 'in' }[]
  isExpanded: boolean
  catColor: (cat?: string) => string
  categoryLabel: (cat?: string) => string
}>()

const emit = defineEmits<{
  select: [name: string]
  expand: []
}>()
</script>

<style scoped lang="less">
.kg-detail {
  width: 330px;
  flex-shrink: 0;
  overflow-y: auto;
  padding: 16px;
  background: var(--color-card);
  border-left: 1px solid rgba(var(--color-gold-rgb), 0.3);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-primary);
  letter-spacing: 0.04em;
  margin: 0 0 12px;
}

.stat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 14px;

  .stat {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 8px 4px;
    background: var(--bg-paper);
    border: 1px solid rgba(var(--color-gold-rgb), 0.25);
    border-radius: 8px;

    b {
      font-size: 20px;
      font-weight: 800;
      color: var(--color-primary);
      font-family: 'STSong', serif;
    }
    span { font-size: 10px; color: var(--color-ink-muted); }
  }
}

.guide {
  list-style: none;
  padding: 0;
  margin: 0;

  li {
    font-size: 11px;
    color: var(--color-ink-faint);
    line-height: 2;
    padding-left: 12px;
    position: relative;

    &::before {
      content: '·';
      position: absolute;
      left: 2px;
      color: var(--color-gold);
      font-weight: 900;
    }
  }
}

.detail-body { display: flex; flex-direction: column; gap: 10px; }

.detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;

  .detail-title { margin: 0; }
}

.cat-badge {
  font-size: 10px;
  color: #fff;
  padding: 2px 8px;
  border-radius: 9px;
}

.expanded-tag {
  font-size: 10px;
  color: var(--color-primary);
  background: rgba(var(--color-gold-rgb), 0.25);
  padding: 2px 8px;
  border-radius: 9px;
}

.detail-image {
  width: 100%;
  max-height: 180px;
  object-fit: contain;
  background: var(--bg-paper);
  border: 1px solid rgba(var(--color-gold-rgb), 0.25);
  border-radius: 8px;
}

.detail-meta {
  font-size: 11px;
  color: var(--color-ink-muted);
  display: flex;
  gap: 10px;
}

.detail-intro {
  font-size: 12px;
  color: var(--color-ink);
  line-height: 1.8;
  margin: 0;

  &-muted { color: var(--color-ink-faint); }
}

.detail-rels {
  .rels-title {
    font-size: 12px;
    font-weight: 700;
    color: var(--color-primary);
    margin: 4px 0 8px;
  }
  .rels-count {
    font-size: 10px;
    color: #fff;
    background: var(--color-gold);
    padding: 1px 6px;
    border-radius: 8px;
  }
  .rels-list { list-style: none; padding: 0; margin: 0; }
  .rel-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    padding: 4px 0;
  }
  .rel-name { color: var(--color-ink-muted); flex-shrink: 0; }
  .rel-arrow { color: var(--color-gold); }
  .rel-target {
    background: none;
    border: none;
    padding: 0;
    font-size: 11px;
    color: var(--color-accent);
    cursor: pointer;
    font-family: inherit;
    &:hover { text-decoration: underline; }
  }
}

.btn-expand {
  margin-top: 4px;
  padding: 8px 0;
  border: 1px solid rgba(var(--color-gold-rgb), 0.5);
  border-radius: 8px;
  background: var(--bg-paper);
  color: var(--color-primary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  &:hover { background: var(--color-gold); color: #fff; }
}

// ═══ 响应式（窄屏隐藏详情面板,画布占满）═══
@media (max-width: 900px) {
  .kg-detail { display: none; }
}
</style>
