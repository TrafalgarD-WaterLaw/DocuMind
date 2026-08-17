<template>
  <div class="home-container">
    <!-- ── Hero ── -->
    <section class="home-hero">
      <div class="hero-seal">鼎</div>
      <h1 class="home-title">智慧文物探索</h1>
      <p class="home-subtitle">多模态解析 · 混合检索 · 多智能体问答</p>
    </section>

    <!-- ── 数据带：系统规模（数字印章）── -->
    <section class="data-strip">
      <div class="data-item" v-for="item in dataItems" :key="item.label">
        <div class="data-number" :class="{ muted: item.value === null }">
          {{ item.value === null ? '未连接' : item.value.toLocaleString() }}
        </div>
        <div class="data-label">{{ item.label }}</div>
      </div>
    </section>

    <!-- ── 功能入口 ── -->
    <section class="feature-grid">
      <div class="feature-card feature-card--primary" @click="navigateTo('/chat')">
        <div class="card-icon"><el-icon :size="28"><ChatDotRound /></el-icon></div>
        <div class="card-body">
          <h3>智能问答</h3>
          <p>六路混合检索 + 证据锚定引用，回答有据可查</p>
        </div>
        <div class="card-action">开始问答 →</div>
      </div>

      <div class="feature-card" @click="navigateTo('/knowledge')">
        <div class="card-icon"><el-icon :size="28"><Share /></el-icon></div>
        <div class="card-body">
          <h3>知识图谱</h3>
          <p>探索文物与遗址、朝代之间的关联脉络</p>
        </div>
        <div class="card-action">浏览图谱 →</div>
      </div>

      <div class="feature-card" @click="navigateTo('/library')">
        <div class="card-icon"><el-icon :size="28"><FolderOpened /></el-icon></div>
        <div class="card-body">
          <h3>知识库</h3>
          <p>上传 PDF 文档，Docling 多模态解析自动入库</p>
        </div>
        <div class="card-action">管理知识库 →</div>
      </div>

      <div class="feature-card" @click="navigateTo('/chat')">
        <div class="card-icon"><el-icon :size="28"><Picture /></el-icon></div>
        <div class="card-body">
          <h3>图像识别</h3>
          <p>上传文物图片，CLIP 零样本识别 + 图文联合检索</p>
        </div>
        <div class="card-action">上传识别 →</div>
      </div>
    </section>

    <!-- ── 入库工作流 ── -->
    <section class="workflow">
      <div class="workflow-step">
        <div class="step-index">壹</div>
        <h4>庋藏文档</h4>
        <p>上传 PDF 古籍、图录，Docling 解析版面、表格、公式</p>
      </div>
      <div class="workflow-arrow">→</div>
      <div class="workflow-step">
        <div class="step-index">贰</div>
        <h4>建库索引</h4>
        <p>分块入库 Chroma，LLM 生成假设问题，构建混合索引</p>
      </div>
      <div class="workflow-arrow">→</div>
      <div class="workflow-step">
        <div class="step-index">叁</div>
        <h4>智能问答</h4>
        <p>多路召回 + RRF 融合 + 多智能体协作，证据可溯源</p>
      </div>
    </section>

    <!-- 底部装饰 -->
    <div class="home-footer-ornament">
      <div class="ornament-line"></div>
      <div class="ornament-seal">问</div>
      <div class="ornament-line"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ChatDotRound, Share, FolderOpened, Picture } from '@element-plus/icons-vue'
import { fetchStats } from '@/api/stats'

const router = useRouter()

function navigateTo(path: string) {
  router.push(path)
}

// ── 系统数据 ──
const STATS_POLL_MS = 30000  // 首页统计轮询间隔
const stats = ref<{ chunks: number; questions: number; artifacts: number | null; sites: number }>({
  chunks: 0, questions: 0, artifacts: null, sites: 0,
})

const dataItems = computed(() => [
  { label: '知识切片', value: stats.value.chunks },
  { label: '假设问题', value: stats.value.questions },
  { label: '图谱文物', value: stats.value.artifacts },
  { label: '考古遗址', value: stats.value.sites },
])

async function loadStats() {
  try {
    const data = await fetchStats()
    stats.value = {
      chunks: data.chunks,
      questions: data.questions,
      artifacts: data.graph ? data.graph.artifacts : null,
      sites: data.graph ? data.graph.sites : 0,
    }
  } catch {
    // 后端未启动时保持占位
  }
}

let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  loadStats()
  timer = setInterval(loadStats, STATS_POLL_MS)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped lang="less">
.home-container {
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 44px 40px 56px;
}

/* ── Hero ── */
.home-hero {
  text-align: center;
  margin-bottom: 36px;

  .hero-seal {
    width: 64px;
    height: 64px;
    margin: 0 auto 18px;
    border: 3px double var(--color-accent);
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'STSong', 'SimSun', serif;
    font-size: 30px;
    font-weight: 700;
    color: var(--color-accent);
    background: rgba(196, 30, 58, 0.05);
    box-shadow: inset 0 0 0 1px rgba(196, 30, 58, 0.15);
    letter-spacing: 0;
  }

  .home-title {
    font-family: 'STSong', 'SimSun', serif;
    font-size: 42px;
    font-weight: 900;
    color: var(--color-primary);
    letter-spacing: 8px;
    margin-bottom: 12px;
  }

  .home-subtitle {
    font-size: 15px;
    color: var(--color-ink);
    opacity: 0.55;
    letter-spacing: 4px;
  }
}

/* ── 数据带：数字印章 ── */
.data-strip {
  display: flex;
  gap: 14px;
  margin-bottom: 40px;
  width: 100%;
  max-width: 860px;
  justify-content: center;

  .data-item {
    flex: 1;
    max-width: 190px;
    text-align: center;
    padding: 18px 12px 14px;
    background: var(--color-card);
    border: 1px solid rgba(var(--color-gold-rgb), 0.4);
    border-radius: 8px;
    position: relative;
    overflow: hidden;
    transition: transform 0.25s ease, box-shadow 0.25s ease;

    &::before {
      content: '';
      position: absolute;
      top: 0;
      left: 25%;
      right: 25%;
      height: 2px;
      background: linear-gradient(90deg, transparent, var(--color-gold), transparent);
    }

    &:hover {
      transform: translateY(-3px);
      box-shadow: 0 6px 20px rgba(139, 69, 19, 0.1);
    }
  }

  .data-number {
    font-family: 'STSong', 'SimSun', serif;
    font-size: 34px;
    font-weight: 700;
    color: var(--color-primary);
    line-height: 1.1;
    letter-spacing: 2px;

    &.muted {
      font-size: 15px;
      color: #b0a08a;
      padding-top: 10px;
    }
  }

  .data-label {
    margin-top: 8px;
    font-size: 12px;
    color: var(--color-gold);
    letter-spacing: 3px;
    font-weight: 500;
  }
}

/* ── 功能入口 ── */
.feature-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  width: 100%;
  max-width: 960px;
  margin-bottom: 44px;

  .feature-card {
    display: flex;
    flex-direction: column;
    padding: 22px 18px 16px;
    background: var(--color-card);
    border: 1px solid rgba(var(--color-gold-rgb), 0.4);
    border-radius: 10px;
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94);
    position: relative;

    &:hover {
      transform: translateY(-4px);
      border-color: var(--color-gold);
      box-shadow: 0 10px 28px rgba(139, 69, 19, 0.12);
    }

    &--primary {
      border-color: rgba(196, 30, 58, 0.35);
      background: linear-gradient(180deg, #fff8f4 0%, var(--color-card) 100%);

      .card-action {
        background: var(--color-accent);
        color: #fff;

        &:hover { background: #d43a4e; }
      }

      &:hover {
        border-color: var(--color-accent);
        box-shadow: 0 10px 28px rgba(196, 30, 58, 0.15);
      }
    }
  }

  .card-icon {
    width: 52px;
    height: 52px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-accent);
    background: rgba(196, 30, 58, 0.06);
    border: 1px solid rgba(196, 30, 58, 0.12);
    margin-bottom: 14px;
    transition: all 0.25s;
  }

  .feature-card:hover .card-icon {
    background: rgba(196, 30, 58, 0.1);
  }

  .card-body {
    flex: 1;

    h3 {
      font-family: 'STSong', 'SimSun', serif;
      font-size: 17px;
      font-weight: 700;
      color: var(--color-primary);
      margin-bottom: 8px;
      letter-spacing: 2px;
    }

    p {
      font-size: 12.5px;
      color: var(--color-ink);
      opacity: 0.6;
      line-height: 1.7;
      margin-bottom: 16px;
    }
  }

  .card-action {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-primary);
    background: rgba(var(--color-gold-rgb), 0.12);
    border-radius: 6px;
    padding: 8px 0;
    text-align: center;
    letter-spacing: 1px;
    transition: all 0.25s;
  }
}

/* ── 入库工作流 ── */
.workflow {
  display: flex;
  align-items: stretch;
  gap: 8px;
  width: 100%;
  max-width: 860px;
  margin-bottom: 40px;

  .workflow-step {
    flex: 1;
    text-align: center;
    padding: 18px 14px;
    border: 1px dashed rgba(var(--color-gold-rgb), 0.5);
    border-radius: 8px;
    background: rgba(253, 250, 243, 0.6);
  }

  .step-index {
    font-family: 'STSong', 'SimSun', serif;
    font-size: 20px;
    font-weight: 700;
    color: var(--color-gold);
    margin-bottom: 6px;
  }

  h4 {
    font-size: 14px;
    font-weight: 600;
    color: var(--color-primary);
    margin-bottom: 6px;
    letter-spacing: 2px;
  }

  p {
    font-size: 11.5px;
    color: var(--color-ink);
    opacity: 0.6;
    line-height: 1.6;
  }

  .workflow-arrow {
    align-self: center;
    color: var(--color-gold);
    font-size: 18px;
    padding: 0 2px;
  }
}

/* ── Footer ornament ── */
.home-footer-ornament {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  width: 100%;
  max-width: 360px;

  .ornament-line {
    flex: 1;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--color-gold), transparent);
  }

  .ornament-seal {
    flex-shrink: 0;
    width: 32px;
    height: 32px;
    border: 1.5px solid var(--color-gold);
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'STSong', 'SimSun', serif;
    font-size: 14px;
    font-weight: 700;
    color: var(--color-gold);
    background: var(--color-card);
  }
}

/* ── Responsive ── */
@media (max-width: 1024px) {
  .feature-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .workflow {
    flex-direction: column;

    .workflow-arrow {
      transform: rotate(90deg);
    }
  }
}

@media (max-width: 640px) {
  .home-container { padding: 28px 16px 40px; }

  .home-hero .home-title { font-size: 30px; letter-spacing: 4px; }

  .data-strip {
    flex-wrap: wrap;

    .data-item { max-width: 45%; }
  }

  .feature-grid { grid-template-columns: 1fr; }
}
</style>
