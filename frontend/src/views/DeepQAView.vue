<template>
  <div class="deep-qa-container">
    <ChatPanel class="chat-area" />
    <SidePanel class="side-area" />
  </div>
</template>

<script setup lang="ts">
import ChatPanel from '@/components/ChatPanel.vue'
import SidePanel from '@/components/SidePanel.vue'
</script>

<style scoped lang="less">
.deep-qa-container {
  display: flex;
  height: 100%;
  width: 100%;
  overflow: hidden;
  background: var(--bg-paper);
}

.chat-area {
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.side-area {
  width: 420px;
  flex-shrink: 0;
  border-left: 1px solid var(--color-gold);
}

@media (max-width: 1024px) {
  .deep-qa-container {
    flex-direction: column;
  }

  .side-area {
    width: 100%;
    height: 300px;
    border-left: none;
    border-top: 1px solid var(--color-gold);
  }
}
</style>
