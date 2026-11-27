<script setup lang="ts">
import { computed, ref } from "vue";

const installers = [
  { name: "pip", command: "pip install proclet" },
  { name: "uv", command: "uv add proclet" },
  { name: "poetry", command: "poetry add proclet" },
  { name: "pdm", command: "pdm add proclet" },
  { name: "pipenv", command: "pipenv install proclet" },
];

const selected = ref(0);
const command = computed(() => installers[selected.value].command);
const copied = ref(false);
let reset: ReturnType<typeof setTimeout> | undefined;

function select(index: number) {
  selected.value = index;
  copied.value = false;
}

function onKeydown(event: KeyboardEvent) {
  const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
  if (!step) return;
  event.preventDefault();
  const next = (selected.value + step + installers.length) % installers.length;
  select(next);
  const tabs = (event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>("[role=tab]");
  tabs[next]?.focus();
}

async function copy() {
  try {
    await navigator.clipboard.writeText(command.value);
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
    <div class="install-tabs" role="tablist" aria-label="Package manager" @keydown="onKeydown">
      <button
        v-for="(installer, index) in installers"
        :key="installer.name"
        type="button"
        role="tab"
        class="install-tab"
        :id="`install-tab-${installer.name}`"
        :aria-selected="index === selected"
        aria-controls="install-panel"
        :tabindex="index === selected ? 0 : -1"
        @click="select(index)"
      >
        {{ installer.name }}
      </button>
    </div>
    <div
      id="install-panel"
      class="install-box"
      role="tabpanel"
      :aria-labelledby="`install-tab-${installers[selected].name}`"
    >
      <code class="install-command"><span class="install-prompt" aria-hidden="true">$</span> {{ command }}</code>
      <button type="button" class="install-copy" @click="copy">
        {{ copied ? "Copied" : "Copy" }}
      </button>
    </div>
    <span class="visually-hidden" aria-live="polite">{{ copied ? "Install command copied" : "" }}</span>
  </div>
</template>

<style scoped>
.install {
  margin-top: 28px;
  max-width: 100%;
}

.install-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: center;
  margin-bottom: 8px;
}

@media (min-width: 960px) {
  .install-tabs {
    justify-content: flex-start;
  }
}

.install-tab {
  padding: 2px 10px;
  border-radius: 6px;
  color: var(--vp-c-text-2);
  font-family: var(--vp-font-family-mono);
  font-size: 13px;
  transition:
    color 0.2s,
    background-color 0.2s;
}

.install-tab:hover {
  color: var(--vp-c-text-1);
}

.install-tab[aria-selected="true"] {
  color: var(--vp-c-brand-1);
  background: var(--vp-c-brand-soft);
}

.install-tab:focus-visible {
  outline: 2px solid var(--vp-c-brand-1);
  outline-offset: 2px;
}

.install-box {
  display: inline-flex;
  align-items: stretch;
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
