<template>
  <div :class="['message-item', message.role]">
    <div class="message-avatar">
      <el-avatar
        v-if="message.role === 'user'"
        :size="36"
        :icon="UserFilled"
        style="background: #c9a96e"
      />
      <el-avatar
        v-else
        :size="36"
        :icon="Service"
        style="background: #8b4513"
      />
    </div>

    <div class="message-body">
      <!-- 研究计划（markdown_dict 事件——深度模式专家分工；F3: MindMapCard
           删除后补渲染，此前数据被收集持久化但永远不展示） -->
      <el-collapse v-if="message.markdownDict" v-model="planOpen" class="plan-collapse">
        <el-collapse-item>
          <template #title>
            <span class="steps-title">
              📋 研究计划（{{ message.markdownDict.mode === 'deep' ? '深度研究' : '快速研究' }}）
            </span>
          </template>
          <div class="plan-body">
            <div v-for="(s, si) in message.markdownDict.sections" :key="si" class="plan-section">
              <div class="plan-title">{{ s.title }}</div>
              <div class="plan-content">{{ s.content }}</div>
            </div>
            <div v-if="message.markdownDict.related_questions?.length" class="plan-related">
              <div class="plan-title">相关问题</div>
              <div v-for="(q, qi) in message.markdownDict.related_questions" :key="qi" class="plan-related-q">
                {{ q }}
              </div>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>

      <!-- Reasoning text（深度研究消息不显示——专家内容在"研究计划"折叠
           已展示,正文只留综合报告,避免重复） -->
      <div v-if="message.reasoning && !message.markdownDict" class="reasoning-text">
        {{ message.reasoning }}
      </div>

      <!-- 图像识别徽章（vision 联动） -->
      <div v-if="message.recognition" class="recognition-badge">
        <span class="badge-icon">🔍</span>
        <span class="badge-text">
          识别为 <strong>{{ message.recognition.result }}</strong>
        </span>
        <span v-if="message.recognition.introduce" class="badge-intro">
          {{ message.recognition.introduce }}
        </span>
      </div>

      <!-- 用户上传的图片（vision 问答） -->
      <img v-if="message.image" :src="message.image" alt="上传图片" class="user-image" />

      <!-- Message content -->
      <div class="message-content">
        <div v-if="message.isStreaming && !message.content" class="streaming-indicator">
          <el-skeleton :rows="2" animated />
        </div>
        <div
          v-else
          class="message-text"
          v-html="renderMarkdown(message.content)"
          @click="onCiteClick"
          @mouseover="onCiteHover"
          @mouseout="onCiteHoverEnd"
          @mouseleave="citeHover = null"
        />
      </div>

      <!-- 引用 hover 缩略图（悬浮 [N] 弹出该源首图） -->
      <div
        v-if="citeHover"
        class="cite-tooltip"
        :style="{ left: citeHover.x + 'px', top: citeHover.y + 'px' }"
      >
        <img :src="citeHover.img" alt="引用来源图片" />
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { imageAbsUrl } from '@/utils/images'
import { useChatStore } from '@/stores/chat'
import type { ChatMessage } from '@/types/domain'
import { UserFilled, Service } from '@element-plus/icons-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Configure marked for safe rendering
marked.setOptions({
  breaks: true,
  gfm: true,
})

/**
 * 引用编号 [N] → 可点击标记（生成环节 🟢 引用联动）。
 * (?!\() 排除 markdown 链接语法 [3](url) 的误替换；
 * DOMPurify 默认放行 data-* 属性，data-cite 可存活。
 */
const CITE_RE = /\[(\d{1,2})\](?!\()/g

function renderMarkdown(text: string): string {
  if (!text) return ''
  // 引用标记仅助手消息生效——用户消息里的 [N] 是原样文本，不该变成金色徽章
  const cited = props.message.role === 'assistant'
    ? text.replace(CITE_RE, '<sup class="cite" data-cite="$1">[$1]</sup>')
    : text
  // DOMPurify 过滤原始 HTML，防 XSS 注入（用户输入/LLM 输出均经过）
  return DOMPurify.sanitize(marked.parse(cited) as string)
}

const props = defineProps<{
  message: ChatMessage
}>()

const chatStore = useChatStore()

// 研究计划折叠默认收起——回答直接展示综合报告(markdown 渲染),专家原文按需展开
const planOpen = ref<string[]>([])

// ── 引用联动：点击 [N] 聚焦证据面板 ──

/** 按引用编号找对应证据源的图片（hover 缩略图用） */
function citationImage(n: number): string {
  const srcs = props.message.sources || []
  for (let i = 0; i < srcs.length; i++) {
    const idx = srcs[i].index || i + 1
    if (idx === n) {
      const rel = srcs[i].images?.[0] || srcs[i].image_url || ''
      return rel ? imageAbsUrl(rel) : ''
    }
  }
  return ''
}

function onCiteClick(e: MouseEvent) {
  const cite = (e.target as HTMLElement).closest('.cite') as HTMLElement | null
  if (!cite) return
  const n = Number(cite.dataset.cite)
  if (n > 0) chatStore.focusCitation(props.message.id, n)
}

// ── hover 缩略图（固定定位浮窗，pointer-events 穿透不抢事件）──
const citeHover = ref<{ img: string; x: number; y: number } | null>(null)

function onCiteHover(e: MouseEvent) {
  const cite = (e.target as HTMLElement).closest('.cite') as HTMLElement | null
  if (!cite) return
  const img = citationImage(Number(cite.dataset.cite))
  if (!img) return
  const rect = cite.getBoundingClientRect()
  // 浮窗 140×110，光标上方弹出，钳制在视口内
  citeHover.value = {
    img,
    x: Math.max(8, Math.min(rect.left + rect.width / 2 - 70, window.innerWidth - 148)),
    y: Math.max(8, rect.top - 118),
  }
}

function onCiteHoverEnd(e: MouseEvent) {
  if ((e.target as HTMLElement).closest('.cite')) citeHover.value = null
}

</script>

<style scoped lang="less">
.message-item {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;

  &.user {
    flex-direction: row-reverse;

    .message-body {
      align-items: flex-end;
    }

    .message-content {
      background: #e8d5c4;
      color: var(--color-ink);
      border-radius: 12px 18px 8px 12px;
      box-shadow: 0 2px 8px rgba(139, 69, 19, 0.12);
    }
  }

  &.assistant {
    .message-body {
      position: relative;
    }

    .message-body::before {
      content: '';
      position: absolute;
      top: 14px;
      left: -18px;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--color-gold);
      box-shadow: 0 0 4px rgba(var(--color-gold-rgb), 0.5);
    }

    .message-content {
      background: var(--color-card);
      color: var(--color-ink);
      border-left: 2px solid var(--color-gold);
      border-radius: 4px 12px 12px 12px;
      box-shadow: 0 2px 6px rgba(139, 69, 19, 0.06);
    }
  }
}

.message-avatar {
  flex-shrink: 0;
  padding-top: 2px;
}

.message-body {
  display: flex;
  flex-direction: column;
  max-width: 70%;
  min-width: 120px;
}

.reasoning-text {
  font-size: 12px;
  color: var(--color-gold);
  font-style: italic;
  margin-bottom: 8px;
  padding: 8px 12px;
  background: rgba(var(--color-gold-rgb), 0.06);
  border-radius: 6px;
  border: 1px dashed rgba(var(--color-gold-rgb), 0.3);
  line-height: 1.6;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.message-content {
  padding: 12px 16px;
  line-height: 1.8;
  word-wrap: break-word;
  font-size: 14px;
}

/* 用户上传图片（vision 问答） */
.user-image {
  max-width: 240px;
  max-height: 180px;
  border-radius: 8px;
  border: 1px solid rgba(var(--color-gold-rgb), 0.4);
  margin: 10px 16px 0;
  display: block;
}

.message-text {
  font-size: 14px;

  // 引用标记 [N]：金色圆角胶囊，可点击聚焦证据面板
  :deep(.cite) {
    display: inline-block;
    margin: 0 1px;
    padding: 0 4px;
    font-size: 11px;
    font-weight: 600;
    line-height: 1.6;
    color: var(--color-primary);
    background: rgba(var(--color-gold-rgb), 0.16);
    border: 1px solid rgba(var(--color-gold-rgb), 0.4);
    border-radius: 4px;
    cursor: pointer;
    vertical-align: super;
    transition: all 0.2s;

    &:hover {
      color: #fff;
      background: var(--color-primary);
      border-color: var(--color-primary);
    }
  }

  // Markdown rendered content styles
  :deep(h1), :deep(h2), :deep(h3) {
    margin: 8px 0 4px;
    font-family: 'STSong', 'SimSun', serif;
    color: var(--color-primary);
  }

  :deep(h1) { font-size: 20px; }
  :deep(h2) { font-size: 17px; }
  :deep(h3) { font-size: 15px; }

  :deep(p) {
    margin: 4px 0;
  }

  :deep(ul), :deep(ol) {
    padding-left: 20px;
    margin: 4px 0;
  }

  :deep(li) {
    margin: 2px 0;
  }

  :deep(code) {
    background: rgba(var(--color-gold-rgb), 0.1);
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 13px;
  }

  :deep(pre) {
    background: rgba(0, 0, 0, 0.05);
    padding: 8px 12px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 13px;
    margin: 8px 0;
  }

  :deep(blockquote) {
    border-left: 3px solid var(--color-gold);
    padding-left: 12px;
    margin: 8px 0;
    color: #666;
  }

  :deep(strong) {
    color: var(--color-primary);
    font-weight: 600;
  }

  :deep(a) {
    color: var(--color-accent);
  }

  :deep(table) {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;

    th, td {
      border: 1px solid rgba(var(--color-gold-rgb), 0.3);
      padding: 6px 10px;
      text-align: left;
      font-size: 13px;
    }

    th {
      background: rgba(var(--color-gold-rgb), 0.1);
      font-weight: 600;
    }
  }
}

.streaming-indicator {
  width: 200px;
}

// ========== 引用 hover 缩略图 ==========
.cite-tooltip {
  position: fixed;
  z-index: 200;
  width: 140px;
  height: 110px;
  padding: 4px;
  background: var(--color-card);
  border: 1px solid rgba(var(--color-gold-rgb), 0.5);
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(139, 69, 19, 0.25);
  pointer-events: none;

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    border-radius: 5px;
  }
}

// ========== 图像识别徽章 ==========
.recognition-badge {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
  padding: 6px 12px;
  background: rgba(196, 30, 58, 0.05);
  border: 1px solid rgba(196, 30, 58, 0.2);
  border-left: 3px solid var(--color-accent);
  border-radius: 6px;
  font-size: 12px;

  .badge-icon {
    font-size: 13px;
  }

  .badge-text {
    color: var(--color-ink);

    strong {
      color: var(--color-accent);
      font-weight: 600;
    }
  }

  .badge-intro {
    color: #999;
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 60%;
  }
}

// ========== 研究计划（深度/快速研究专家分工） ==========
// 覆盖 el-collapse 默认纯白样式——透明底 + 金色描边,与消息整体风格一致
.plan-collapse {
  margin-bottom: 10px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid rgba(var(--color-gold-rgb), 0.35);
  background: rgba(253, 250, 243, 0.35);

  :deep(.el-collapse-item__header) {
    height: 40px;
    line-height: 40px;
    padding: 0 12px;
    background: transparent;
    border-bottom: 1px dashed rgba(var(--color-gold-rgb), 0.3);
    color: var(--color-primary);
    font-weight: 600;
    font-size: 13px;
  }

  :deep(.el-collapse-item__wrap) {
    background: transparent;
    border-bottom: none;
  }

  :deep(.el-collapse-item__content) {
    padding: 10px 12px;
    background: transparent;
  }
}

.plan-body { padding: 0; }
.plan-section { margin-bottom: 10px; }
.plan-title { font-weight: 600; font-size: 12.5px; color: var(--color-primary); margin-bottom: 4px; }
.plan-content {
  font-size: 12.5px;
  color: #6b5a3f;
  white-space: pre-wrap;
  line-height: 1.7;
  word-wrap: break-word;
}
.plan-related { border-top: 1px dashed rgba(var(--color-gold-rgb), 0.3); padding-top: 6px; }
.plan-related-q { font-size: 12px; color: #8a7a5f; padding: 2px 0; }
</style>
