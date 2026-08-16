import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('@/views/HomeView.vue'),
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/DeepQAView.vue'),
    // keep-alive 生效靠 AppMain 的 include=['DeepQAView'](组件名=文件名)
  },
  // 兼容旧路径
  {
    path: '/deep-qa',
    redirect: '/chat',
  },
  {
    path: '/knowledge',
    name: 'KnowledgeGraph',
    component: () => import('@/views/KnowledgeGraphView.vue'),
  },
  {
    path: '/library',
    name: 'Library',
    component: () => import('@/views/LibraryView.vue'),
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
