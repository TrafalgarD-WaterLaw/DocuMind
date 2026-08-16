import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'

// 修复 Edge 浏览器最小化后自动弹出窗口的问题
const isEdge = /Edg\//.test(navigator.userAgent)
if (isEdge) {
  const originalReplaceState = history.replaceState
  history.replaceState = function (...args: any[]) {
    if (document.visibilityState === 'hidden') return
    return originalReplaceState.apply(this, args as any)
  }
}

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.mount('#app')
