<template>
  <div class="chat-panel">
    <!-- Session sidebar -->
    <div class="session-bar">
      <button class="new-chat-btn" @click="chatStore.newSession()" :disabled="chatStore.isStreaming">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        <span>新对话</span>
      </button>
      <div class="session-list">
        <div
          v-for="s in chatStore.sessions"
          :key="s.id"
          :class="['session-item', { active: s.id === chatStore.activeId }]"
          @click="chatStore.switchSession(s.id)"
        >
          <span class="session-title">{{ s.title }}</span>
          <button
            class="session-del"
            @click.stop="chatStore.deleteSession(s.id)"
            :disabled="chatStore.isStreaming"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
      </div>
    </div>

    <!-- Main chat area -->
    <div class="chat-main">
      <div class="chat-header">
        <h3>{{ chatStore.activeSession?.title || '深度问答' }}</h3>
        <div class="header-right">
          <label class="deep-toggle" :class="{ active: chatStore.deepMode }">
            <input type="checkbox" :checked="chatStore.deepMode" :disabled="chatStore.isStreaming" @change="chatStore.toggleDeepMode()" />
            <span class="toggle-track">
              <span class="toggle-label off">快速</span>
              <span class="toggle-thumb" />
              <span class="toggle-label on">深度</span>
            </span>
          </label>
          <button class="clear-btn" @click="chatStore.clearMessages()" :disabled="chatStore.isStreaming" title="清空对话">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3,6 5,6 21,6"/><path d="M19,6v14a2,2,0,0,1-2,2H7a2,2,0,0,1-2-2V6M8,6V4a2,2,0,0,1,2-2h4a2,2,0,0,1,2,2V6"/></svg>
          </button>
        </div>
      </div>

      <ChatMessageList class="chat-body" :messages="chatStore.messages" />

      <ChatInput
        class="chat-footer"
        :disabled="chatStore.isStreaming"
        @send="chatStore.sendQuery"
        @stop="chatStore.stopGeneration()"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import ChatMessageList from '@/components/ChatMessageList.vue'
import ChatInput from '@/components/ChatInput.vue'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()
</script>

<style scoped lang="less">
.chat-panel {
  height: 100%; display: flex; gap: 1px;
  background: var(--color-gold);
  border: 1px solid var(--color-gold);
  border-top: 4px solid var(--color-gold);
  border-radius: 8px; overflow: hidden;
}

// Session sidebar
.session-bar {
  width: 180px; flex-shrink: 0;
  background: rgba(253, 250, 243, 0.95);
  display: flex; flex-direction: column;
}

.new-chat-btn {
  display: flex; align-items: center; gap: 6px;
  margin: 10px; padding: 8px 12px;
  font-size: 13px; font-weight: 500;
  color: var(--color-primary);
  background: none;
  border: 1px dashed rgba(var(--color-gold-rgb), 0.5);
  border-radius: 6px; cursor: pointer;
  transition: all 0.2s;
  &:hover:not(:disabled) {
    background: rgba(var(--color-gold-rgb), 0.1);
    border-color: var(--color-gold); border-style: solid;
  }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

.session-list {
  flex: 1; overflow-y: auto; padding: 0 8px 8px;
}

.session-item {
  display: flex; align-items: center;
  padding: 8px 10px; margin-bottom: 2px;
  border-radius: 6px; cursor: pointer;
  font-size: 12px; color: var(--color-ink);
  transition: all 0.15s;
  &:hover { background: rgba(var(--color-gold-rgb), 0.08); }
  &.active {
    background: rgba(var(--color-gold-rgb), 0.15);
    font-weight: 600; color: var(--color-primary);
  }
}

.session-title {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.session-del {
  flex-shrink: 0; opacity: 0;
  padding: 2px; border: none; background: none;
  color: #ccc; cursor: pointer; border-radius: 3px;
  transition: all 0.15s;
  .session-item:hover & { opacity: 1; }
  &:hover { color: var(--color-accent); background: rgba(196, 30, 58, 0.08); }
}

// Main chat area
.chat-main {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column;
  background: rgba(253, 250, 243, 0.9);
}

.chat-header {
  flex-shrink: 0; display: flex; justify-content: space-between; align-items: center;
  padding: 12px 20px 10px;
  border-bottom: 1px solid rgba(var(--color-gold-rgb), 0.3);

  h3 { font-size: 16px; font-weight: 600; color: var(--color-ink); margin: 0; }
}

.header-right { display: flex; align-items: center; gap: 10px; }

.clear-btn {
  display: flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border-radius: 6px;
  border: 1px solid transparent; background: none;
  color: #999; cursor: pointer; transition: all 0.2s;
  &:hover:not(:disabled) { color: var(--color-accent); border-color: rgba(196,30,58,0.2); background: rgba(196,30,58,0.04); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}

// Deep toggle
.deep-toggle {
  display: flex; align-items: center; cursor: pointer;
  input { display: none; }
  .toggle-track {
    display: flex; align-items: center; position: relative;
    width: 96px; height: 26px;
    background: rgba(var(--color-gold-rgb), 0.12);
    border: 1px solid rgba(var(--color-gold-rgb), 0.3); border-radius: 13px;
    transition: all 0.3s;
  }
  .toggle-label {
    flex: 1; text-align: center; font-size: 11px; font-weight: 600; z-index: 1;
    transition: color 0.3s;
  }
  .toggle-thumb {
    position: absolute; top: 1px; left: 1px;
    width: 46px; height: 22px;
    background: var(--color-accent); border-radius: 11px;
    transition: transform 0.3s ease;
  }
  .off { color: var(--color-primary); }
  .on { color: #999; }
  &.active {
    .toggle-thumb { transform: translateX(46px); }
    .toggle-track { background: rgba(196,30,58,0.06); border-color: rgba(196,30,58,0.3); }
    .on { color: #fff; }
    .off { color: #999; }
  }
}

.chat-body { flex: 1 1 0; overflow-y: auto; overflow-x: hidden; min-height: 0; }

.chat-footer {
  flex-shrink: 0; border-top: 1px solid var(--color-gold); padding: 10px 16px;
}
</style>
