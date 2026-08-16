<template>
  <div id="applicationContainer">
    <AppSidebar />
    <AppMain />
  </div>
</template>

<script setup lang="ts">
import AppSidebar from '@/components/AppSidebar.vue'
import AppMain from '@/components/AppMain.vue'
</script>

<style>
/* ── CSS Custom Properties ── */
:root {
  --bg-paper:       #f5f0e8;
  --color-primary:  #8b4513;
  --color-accent:   #c41e3a;
  --color-gold:     #c9a96e;
  --color-ink:      #2c2c2c;
  --color-card:     var(--color-card);
  --color-gold-rgb:  201, 169, 110;
  --color-ink-muted: var(--color-ink-muted);
  --color-ink-faint: var(--color-ink-faint);
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

#applicationContainer {
  display: flex;
  height: 100vh;
  font-family: 'PingFang SC', 'Microsoft YaHei', 'SimSun', serif;
  background: var(--bg-paper);
  color: var(--color-ink);
}

/* ── Typography defaults ── */
h1, h2, h3, h4, h5, h6 {
  font-family: 'STSong', 'SimSun', 'KaiTi', serif;
  color: var(--color-primary);
}

@media (max-width: 768px) {
  #applicationContainer {
    flex-direction: column;
  }
}
</style>
