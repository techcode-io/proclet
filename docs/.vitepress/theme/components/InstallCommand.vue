<script setup lang="ts">
import { ref } from "vue";

const command = "pip install proclet";
const copied = ref(false);
let reset: ReturnType<typeof setTimeout> | undefined;

async function copy() {
  try {
    await navigator.clipboard.writeText(command);
  } catch {
    return;
  }
  copied.value = true;
  clearTimeout(reset);
  reset = setTimeout(() => (copied.value = false), 2000);
}
</script>

<template>
  <div class="install">
    <code class="install-command"><span class="install-prompt" aria-hidden="true">$</span> {{ command }}</code>
    <button type="button" class="install-copy" @click="copy">
      {{ copied ? "Copied" : "Copy" }}
    </button>
    <span class="visually-hidden" aria-live="polite">{{ copied ? "Install command copied" : "" }}</span>
  </div>
</template>

<style scoped>
.install {
  display: inline-flex;
  align-items: stretch;
  margin-top: 28px;
  border: 1px solid var(--pc-pipe-wall);
  border-radius: 8px;
  background: var(--vp-c-bg);
  font-family: var(--vp-font-family-mono);
  font-size: 15px;
  max-width: 100%;
}

.install-command {
  padding: 10px 16px;
  color: var(--vp-c-text-1);
  white-space: nowrap;
  overflow-x: auto;
}

.install-prompt {
  color: var(--vp-c-text-3);
  user-select: none;
}

.install-copy {
  padding: 0 16px;
  border-left: 1px solid var(--pc-pipe-wall);
  color: var(--vp-c-brand-1);
  font-family: var(--vp-font-family-base);
  font-size: 14px;
  font-weight: 600;
  min-width: 84px;
  border-radius: 0 7px 7px 0;
  transition: background-color 0.2s;
}

.install-copy:hover {
  background: var(--vp-c-brand-soft);
}

.install-copy:focus-visible {
  outline: 2px solid var(--vp-c-brand-1);
  outline-offset: 2px;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}
</style>
