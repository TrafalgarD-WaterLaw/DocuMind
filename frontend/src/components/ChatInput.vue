<template>
  <div class="chat-input">
    <!-- Image preview -->
    <div v-if="previewUrl" class="img-preview">
      <img :src="previewUrl" alt="预览" />
      <button class="img-remove" @click="clearImage" :disabled="disabled">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>

    <!-- Input row -->
    <div class="input-row">
      <label class="upload-btn" :class="{ disabled }">
        <input ref="fileInput" type="file" accept="image/*" :disabled="disabled" @change="onFilePicked" />
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21,15 16,10 5,21"/></svg>
      </label>

      <el-input
        ref="inputRef"
        v-model="inputText"
        type="textarea"
        :rows="2"
        :disabled="disabled"
        :placeholder="disabled ? '正在生成回答...' : selectedFile ? '输入描述（可选）...' : '输入你的文物研究问题...'"
        resize="none"
        @keydown.enter.exact.prevent="handleSend"
      />

      <el-button
        v-if="!disabled"
        type="primary"
        :icon="Promotion"
        :disabled="!inputText.trim() && !selectedFile"
        @click="handleSend"
      >
        发送
      </el-button>
      <el-button
        v-else
        type="danger"
        :icon="Close"
        @click="$emit('stop')"
      >
        停止
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { Promotion, Close } from '@element-plus/icons-vue'

defineProps<{ disabled: boolean }>()

const emit = defineEmits<{
  send: [query: string, imageFile?: File]
  stop: []
}>()

const inputText = ref('')
const inputRef = ref()
const fileInput = ref<HTMLInputElement>()
const selectedFile = ref<File | null>(null)
const previewUrl = ref<string | null>(null)

function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  selectedFile.value = file
  previewUrl.value = URL.createObjectURL(file)
}

function clearImage() {
  selectedFile.value = null
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = null
  if (fileInput.value) fileInput.value.value = ''
}

async function handleSend() {
  const query = inputText.value.trim()
  if (!query && !selectedFile.value) return

  // 图片直传后端 /api/vision/chat（识别 → 检索 → 流式回答在服务端完成）
  emit('send', query, selectedFile.value || undefined)
  inputText.value = ''
  clearImage()
  nextTick(() => inputRef.value?.focus())
}
</script>

<style scoped lang="less">
.chat-input {
  display: flex; flex-direction: column;
  padding-top: 12px;
  border-top: 1px solid var(--color-gold);
}

.img-preview {
  position: relative; display: inline-block;
  margin-bottom: 8px;
  img {
    max-height: 80px; border-radius: 6px;
    border: 1px solid var(--color-gold);
  }
}

.img-remove {
  position: absolute; top: -6px; right: -6px;
  width: 20px; height: 20px; border-radius: 50%;
  background: var(--color-accent); color: #fff;
  border: none; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: transform 0.15s;
  &:hover:not(:disabled) { transform: scale(1.2); }
  &:disabled { opacity: 0.5; }
}

.input-row {
  display: flex; gap: 10px; align-items: flex-end;
}

.upload-btn {
  flex-shrink: 0;
  width: 40px; height: 40px;
  border: 1px dashed rgba(var(--color-gold-rgb), 0.5);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  color: var(--color-gold); cursor: pointer;
  transition: all 0.2s;
  input { display: none; }
  &:hover:not(.disabled) {
    border-color: var(--color-gold); border-style: solid;
    background: rgba(var(--color-gold-rgb), 0.08);
  }
  &.disabled { opacity: 0.4; cursor: not-allowed; }
}

.el-textarea { flex: 1; }

:deep(.el-textarea__inner) {
  background: var(--color-card);
  border: 1px solid rgba(var(--color-gold-rgb), 0.4);
  border-radius: 8px;
  color: var(--color-ink);
  font-size: 14px; line-height: 1.6;
  padding: 10px 14px;
  transition: border-color 0.3s ease, box-shadow 0.3s ease;
  &::placeholder { color: rgba(var(--color-gold-rgb), 0.6); }
  &:focus {
    border-color: var(--color-gold);
    box-shadow: 0 0 0 2px rgba(var(--color-gold-rgb), 0.15);
  }
}

:deep(.el-button) {
  flex-shrink: 0; height: 40px; font-weight: 500;
  border-radius: 8px; padding: 0 20px;
  transition: all 0.3s ease;

  &.el-button--primary {
    background: var(--color-accent); border-color: var(--color-accent); color: #fff;
    &:hover:not(:disabled) {
      box-shadow: 0 0 12px rgba(196, 30, 58, 0.35);
      transform: translateY(-1px);
    }
    &:disabled {
      background: rgba(var(--color-gold-rgb), 0.3);
      border-color: rgba(var(--color-gold-rgb), 0.2);
      color: rgba(var(--color-gold-rgb), 0.5);
    }
  }
  &.el-button--danger { animation: pulse-stop 1.5s ease-in-out infinite; }
}

@keyframes pulse-stop {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
</style>
