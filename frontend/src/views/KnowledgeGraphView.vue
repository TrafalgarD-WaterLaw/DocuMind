<template>
  <div class="kg-view">
    <KnowledgeGraphPanel />
  </div>
</template>

<script setup lang="ts">
import KnowledgeGraphPanel from '@/components/KnowledgeGraphPanel.vue'
</script>

<style scoped lang="less">
.kg-view {
  height: 100%;
  width: 100%;
  padding: 14px;
  box-sizing: border-box;
  background: var(--bg-paper);
}
</style>
