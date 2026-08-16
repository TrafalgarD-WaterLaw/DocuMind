<template>
  <main class="app-main">
    <router-view v-slot="{ Component }">
      <keep-alive :include="['DeepQAView']">
        <component :is="Component" />
      </keep-alive>
    </router-view>
  </main>
</template>

<script setup lang="ts">
</script>

<style scoped lang="less">
.app-main {
  flex: 1;
  overflow: hidden;
  min-height: 0;
}
</style>
