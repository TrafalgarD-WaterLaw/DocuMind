<template>
  <el-menu
    :default-active="activeRoute"
    class="app-sidebar"
    background-color="var(--color-primary)"
    text-color="#f5f0e0"
    :active-text-color="'var(--color-gold)'"
    :collapse="appStore.sidebarCollapsed"
    router
  >
    <!-- 云纹装饰 -->
    <div class="cloud-pattern"></div>

    <!-- 侧边栏头部 -->
    <div class="sidebar-header">
      <div class="logo-wrapper">
        <span class="logo-seal">鼎</span>
      </div>
      <span v-show="!appStore.sidebarCollapsed" class="sidebar-title">智慧文物探索</span>
    </div>

    <!-- 金色分割线 -->
    <div class="header-divider"></div>

    <el-menu-item index="/">
      <el-icon><HomeFilled /></el-icon>
      <span>首页</span>
    </el-menu-item>

    <el-menu-item index="/chat">
      <el-icon><ChatDotRound /></el-icon>
      <span>智能问答</span>
    </el-menu-item>

    <el-menu-item index="/knowledge">
      <el-icon><Share /></el-icon>
      <span>知识图谱</span>
    </el-menu-item>

    <el-menu-item index="/library">
      <el-icon><FolderOpened /></el-icon>
      <span>知识库</span>
    </el-menu-item>

    <!-- 底部折叠按钮 -->
    <div class="sidebar-footer">
      <div class="collapse-toggle" @click="appStore.toggleSidebar()">
        <span class="huiwen-icon">回</span>
        <span v-show="!appStore.sidebarCollapsed" class="collapse-text">收起菜单</span>
      </div>
    </div>
  </el-menu>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { HomeFilled, ChatDotRound, Share, FolderOpened } from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const appStore = useAppStore()

const activeRoute = computed(() => {
  if (route.path.startsWith('/chat')) return '/chat'
  if (route.path.startsWith('/knowledge')) return '/knowledge'
  if (route.path.startsWith('/library')) return '/library'
  return '/'
})
</script>

<style scoped lang="less">
.app-sidebar {
  height: 100vh;
  width: 220px;
  border-right: 1px solid rgba(var(--color-gold-rgb), 0.3);
  flex-shrink: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;

  &.el-menu--collapse {
    width: 64px;
  }
}

/* ── Cloud pattern decorative strip ── */
.cloud-pattern {
  height: 8px;
  background:
    repeating-linear-gradient(
      90deg,
      transparent,
      transparent 18px,
      rgba(var(--color-gold-rgb), 0.35) 18px,
      rgba(var(--color-gold-rgb), 0.35) 22px
    ),
    linear-gradient(
      180deg,
      rgba(var(--color-gold-rgb), 0.08) 0%,
      transparent 100%
    );
}

/* ── Sidebar header ── */
.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px 12px 16px;
  gap: 10px;

  .logo-wrapper {
    flex-shrink: 0;
    width: 42px;
    height: 42px;
    border: 2px solid var(--color-gold, #c9a96e);
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(var(--color-gold-rgb), 0.12);
  }

  .logo-seal {
    font-family: 'Noto Serif SC', 'STSong', 'SimSun', serif;
    font-size: 22px;
    font-weight: 700;
    color: var(--color-gold, #c9a96e);
    line-height: 1;
  }

  .sidebar-title {
    font-family: 'Noto Serif SC', 'STSong', 'SimSun', serif;
    font-size: 17px;
    font-weight: 700;
    color: #f5f0e0;
    white-space: nowrap;
    letter-spacing: 2px;
  }
}

/* ── Gold divider ── */
.header-divider {
  height: 1px;
  margin: 0 16px 8px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    var(--color-gold, #c9a96e) 15%,
    var(--color-gold, #c9a96e) 85%,
    transparent 100%
  );
}

/* ── Menu item overrides ── */
:deep(.el-menu-item) {
  margin: 2px 8px;
  border-radius: 4px;
  transition: all 0.25s ease;

  &:hover {
    background-color: rgba(var(--color-gold-rgb), 0.12) !important;
    color: #fff !important;
  }

  &.is-active {
    background: linear-gradient(
      90deg,
      rgba(196, 30, 58, 0.18) 0%,
      rgba(196, 30, 58, 0.05) 100%
    ) !important;
    border-left: 3px solid var(--color-accent, #c41e3a) !important;
    color: var(--color-gold, #c9a96e) !important;
    font-weight: 600;
  }
}

/* ── Sidebar footer ── */
.sidebar-footer {
  margin-top: auto;
  padding: 12px 8px 20px;
}

.collapse-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.25s ease;
  color: rgba(245, 240, 224, 0.6);

  &:hover {
    background-color: rgba(var(--color-gold-rgb), 0.12);
    color: #f5f0e0;
  }

  .huiwen-icon {
    font-family: 'Noto Serif SC', 'STSong', 'SimSun', serif;
    font-size: 18px;
    font-weight: 700;
    line-height: 1;
    color: var(--color-gold, #c9a96e);
  }

  .collapse-text {
    font-size: 13px;
    white-space: nowrap;
  }
}
</style>
