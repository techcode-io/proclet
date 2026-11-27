<template>
  <figure class="pipeline">
    <svg
      viewBox="0 0 520 330"
      role="img"
      aria-labelledby="pipeline-title"
      xmlns="http://www.w3.org/2000/svg"
    >
      <title id="pipeline-title">
        pg_dump pipes its output into zstd, which writes shop.sql.zst; both send their
        stderr, line by line, to logging.warning.
      </title>

      <!-- stdout to stdin: raw OS pipes -->
      <g class="pipe">
        <line class="pipe-wall" x1="140" y1="92" x2="208" y2="92" />
        <line class="pipe-flow" x1="140" y1="92" x2="208" y2="92" />
        <line class="pipe-wall" x1="312" y1="92" x2="364" y2="92" />
        <line class="pipe-flow" x1="312" y1="92" x2="364" y2="92" />
      </g>

      <!-- stderr: line by line to a logger -->
      <g class="err">
        <path class="err-line" d="M78 120 V250" />
        <path class="err-line err-line-late" d="M260 120 V250" />
        <text class="err-label" x="90" y="190">stderr</text>
        <text class="err-label" x="272" y="190">stderr</text>
      </g>

      <!-- processes -->
      <g class="node">
        <rect x="16" y="64" width="124" height="56" rx="10" />
        <text x="78" y="97">pg_dump</text>
      </g>
      <g class="node">
        <rect x="208" y="64" width="104" height="56" rx="10" />
        <text x="260" y="97">zstd</text>
      </g>

      <!-- output file -->
      <g class="file">
        <path d="M364 56 H486 L504 74 V128 H364 Z" />
        <path class="file-fold" d="M486 56 V74 H504" />
        <text x="432" y="97">shop.sql.zst</text>
      </g>

      <!-- logger -->
      <g class="sink">
        <rect x="16" y="250" width="296" height="56" rx="10" />
        <text x="164" y="283">logging.warning</text>
      </g>
    </svg>
  </figure>
</template>

<style scoped>
.pipeline {
  width: 100%;
  max-width: 520px;
  margin: 0;
}

svg {
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
}

text {
  font-family: var(--vp-font-family-mono);
  font-size: 15px;
  text-anchor: middle;
  fill: var(--vp-c-text-1);
}

.node rect,
.sink rect,
.file path {
  fill: var(--vp-c-bg);
  stroke: var(--pc-ink-line);
  stroke-width: 1.5;
}

.file .file-fold {
  fill: none;
}

.sink rect {
  stroke: var(--pc-err);
  stroke-dasharray: none;
}

.pipe-wall {
  stroke: var(--pc-pipe-wall);
  stroke-width: 16;
}

.pipe-flow {
  stroke: var(--pc-flow);
  stroke-width: 4;
  stroke-dasharray: 9 9;
  animation: flow 0.9s linear infinite;
}

.err-line {
  fill: none;
  stroke: var(--pc-err);
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-dasharray: 2 12;
  animation: drip 1.6s linear infinite;
}

.err-line-late {
  animation-delay: -0.7s;
}

.err-label {
  font-size: 13px;
  text-anchor: start;
  fill: var(--pc-err-text);
}

@keyframes flow {
  to {
    stroke-dashoffset: -18;
  }
}

@keyframes drip {
  to {
    stroke-dashoffset: -14;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pipe-flow,
  .err-line {
    animation: none;
  }
}
</style>
