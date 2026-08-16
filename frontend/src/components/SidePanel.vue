<template>
  <div class="side-panel">
    <!-- 上部：检索流水线（当前轮次，常驻可见——方案 B 动态点亮） -->
    <div class="panel-title">
      <span class="title-dot"></span>
      检索流水线
      <div v-if="rounds.length > 1" class="round-tabs">
        <button
          v-for="(r, i) in rounds"
          :key="r.msg.id"
          class="round-tab"
          :class="{ active: i === activeRoundIdx }"
          @click="selectRound(i)"
        >第 {{ i + 1 }} 轮</button>
      </div>
    </div>
    <div class="pipeline-area">
      <PipelinePanel
        v-if="activeRound"
        :pipeline="activeRound.msg.pipeline || []"
        :query="activeRound.query"
        :sources="activeRound.msg.sources || []"
      />
      <div v-else class="pipeline-empty">发送问题后，检索流水线将在此实时点亮</div>
    </div>

    <!-- 下部：证据链（当前轮次） -->
    <div class="panel-title lower">
      <span class="title-dot"></span>
      证据链
    </div>
    <div class="evidence-area">
      <EvidencePanel :round="activeRound?.msg" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import type { ChatMessage } from '@/types/domain'
import PipelinePanel from './PipelinePanel.vue'
import EvidencePanel from './EvidencePanel.vue'

const chatStore = useChatStore()

interface RoundItem {
  msg: ChatMessage
  query: string
}

// 轮次：有证据来源或流水线事件的助手回答（pipeline rewrite 事件到达即成轮次——
// 检索期间面板必须挂载，动态点亮才能实时可见）
const rounds = computed<RoundItem[]>(() => {
  const msgs = chatStore.messages
  const out: RoundItem[] = []
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i]
    if (m.role !== 'assistant') continue
    if (!((m.sources && m.sources.length > 0) || (m.pipeline && m.pipeline.length > 0))) {
      continue
    }
    // 相邻前一条 user 消息作为该轮原始问题
    let q = ''
    for (let j = i - 1; j >= 0; j--) {
      if (msgs[j].role === 'user') { q = msgs[j].content; break }
    }
    out.push({ msg: m, query: q })
  }
  return out
})

const activeRoundIdx = ref(0)

function selectRound(i: number) {
  activeRoundIdx.value = i
  userSelectedRound.value = true
}

// F2: 只在新轮次出现时自动跟随（轮次数量增加）——流式期间 pipeline/sources
// 事件会不断触发 rounds 重算，若每次都切最新轮，用户手动查看旧轮会被反复弹回
const userSelectedRound = ref(false)
watch(
  () => rounds.value.length,
  (len, prev) => {
    if (len > (prev || 0) && !userSelectedRound.value) {
      activeRoundIdx.value = len - 1
    }
    // 用户手动选择过轮次后不再自动跟随，直到新问题开始（length 归零后重置）
    if (len === 0) userSelectedRound.value = false
  },
)

// 🟢 引用联动：点击回答正文 [N] → 面板切到该引用所属轮次（EvidencePanel
// 再滚动定位；此处只保证轮次对上，具体滚动由子组件完成）
watch(
  () => chatStore.activeCitation,
  (c) => {
    if (!c) return
    const idx = rounds.value.findIndex(r => r.msg.id === c.msgId)
    if (idx >= 0) {
      activeRoundIdx.value = idx
      userSelectedRound.value = true
    }
  },
)

const activeRound = computed<RoundItem | null>(() => rounds.value[activeRoundIdx.value] || null)
</script>

<style scoped lang="less">
.side-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--color-card);
  border-left: 2px solid var(--color-gold);
}

.panel-title {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px 8px;
  font-size: 13px; font-weight: 600;
  color: var(--color-primary);
  flex-shrink: 0;
  background: rgba(253, 250, 243, 0.8);

  &.lower {
    border-top: 1px solid var(--color-gold);
  }
}

.title-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--color-gold);
}

// 轮次切换
.round-tabs {
  display: flex; gap: 4px; margin-left: auto;
}

.round-tab {
  padding: 2px 8px;
  font-size: 10px; font-weight: 500;
  color: #8a6d3b;
  background: rgba(253, 250, 243, 0.9);
  border: 1px solid rgba(var(--color-gold-rgb), 0.35);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s;
  &:hover { border-color: var(--color-gold); }
  &.active {
    color: #fff;
    background: var(--color-primary);
    border-color: var(--color-primary);
  }
}

.pipeline-area {
  flex-shrink: 0;
  height: 260px;
  overflow-y: auto;
  border-bottom: 1px solid rgba(var(--color-gold-rgb), 0.15);
}

.pipeline-empty {
  height: 100%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: var(--color-ink-faint);
}

.evidence-area {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
