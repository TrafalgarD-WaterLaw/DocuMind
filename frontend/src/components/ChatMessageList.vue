<template>
  <div ref="listRef" class="message-list" @scroll="onScroll">
    <div v-if="messages.length === 0" class="empty-state">
      <el-icon :size="48" color="#c9a96e"><ChatDotRound /></el-icon>
      <p>开始一段新的文物研究对话</p>
    </div>

    <ChatMessageItem
      v-for="message in messages"
      :key="message.id"
      :message="message"
    />

    <!-- Scroll-to-bottom button -->
    <Transition name="fade">
      <button v-if="showScrollBtn" class="scroll-btn" @click="scrollToBottom">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>
      </button>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { ChatMessage } from '@/types/domain'
import ChatMessageItem from '@/components/ChatMessageItem.vue'
import { ChatDotRound } from '@element-plus/icons-vue'

const props = defineProps<{
  messages: ChatMessage[]
}>()

const listRef = ref<HTMLElement | null>(null)
const showScrollBtn = ref(false)
let userScrolledUp = false

const SCROLL_THRESHOLD_PX = 80  // 距底部多近视为"在底部"(流式跟随判定)

function isNearBottom(): boolean {
  const el = listRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_THRESHOLD_PX
}

function scrollToBottom() {
  nextTick(() => {
    if (listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
      userScrolledUp = false
      showScrollBtn.value = false
    }
  })
}

function onScroll() {
  const near = isNearBottom()
  userScrolledUp = !near
  showScrollBtn.value = !near
}

// New message added → scroll down (user hasn't scrolled up)
watch(
  () => props.messages.length,
  () => { if (!userScrolledUp) scrollToBottom() },
)

// Content streaming → only scroll if user is near bottom
watch(
  () => props.messages.map((m) => m.content).join(''),
  () => { if (!userScrolledUp) scrollToBottom() },
)
</script>

<style scoped lang="less">
.message-list {
  padding: 20px 16px;
  scroll-behavior: smooth;
  position: relative;

  background:
    repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(139, 69, 19, 0.02) 2px, rgba(139, 69, 19, 0.02) 4px),
    linear-gradient(135deg, rgba(253, 250, 243, 0.9) 0%, rgba(245, 240, 232, 0.8) 50%, rgba(253, 250, 243, 0.9) 100%);

  &::-webkit-scrollbar { width: 6px; }
  &::-webkit-scrollbar-track { background: rgba(245, 240, 232, 0.5); border-radius: 3px; }
  &::-webkit-scrollbar-thumb { background: var(--color-gold); border-radius: 3px; }
  scrollbar-width: thin;
  scrollbar-color: var(--color-gold) rgba(245, 240, 232, 0.5);
}

.empty-state {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; height: 100%; gap: 12px;
  p { font-size: 14px; color: var(--color-gold); }
}

// Scroll-to-bottom button
.scroll-btn {
  position: sticky;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  width: 36px; height: 36px;
  border-radius: 50%;
  background: var(--color-primary);
  color: #fff;
  border: 2px solid var(--color-gold);
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 2px 8px rgba(139, 69, 19, 0.25);
  transition: all 0.2s;
  z-index: 10;
  &:hover {
    background: var(--color-accent);
    transform: translateX(-50%) scale(1.1);
  }
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
