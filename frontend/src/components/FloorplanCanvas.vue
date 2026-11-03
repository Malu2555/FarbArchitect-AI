<script setup>
/**
 * 100x100 grid painted onto a 500x500 HTML5 canvas (5 px per cell).
 *
 * Layers, painted back-to-front:
 *   1. Base grid + background
 *   2. Orange thermal overlay (cells showing peak heating)
 *   3. Red vibration halos (pairwise proximity)
 *   4. Teal dashed machine footprints (optimized layout)
 *   5. Legend card
 *
 * Redraws are reactive + coalesced with requestAnimationFrame.
 */

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

const props = defineProps({
  scene: { type: Object, default: null },
  before: { type: Object, default: null },
  after: { type: Object, default: null },
  // Stage 2 — Muse Image spatial spec (preferred source of geometry).
  spatialSpec: { type: Object, default: null },
  // Corrected layout from Muse Wonder (used if spatial spec is absent).
  finalLayout: { type: Array, default: () => [] },
  // Scenario editor: the user's hand-built layout. When `editable` is true this
  // is authoritative, because the user's own geometry must win over whatever
  // the last simulation produced.
  layout: { type: Array, default: null },
  editable: { type: Boolean, default: false },
  selectionId: { type: String, default: null },
});

const emit = defineEmits([
  'add-equipment',
  'move-equipment',
  'remove-equipment',
  'select-equipment',
]);

const GRID = 100;
const CELL = 5;
const WIDTH = GRID * CELL;
const HEIGHT = GRID * CELL;
const THERMAL_THRESHOLD = 22;
const THERMAL_SPAN = 8;

const canvasEl = ref(null);
let ctx = null;
let rafId = 0;

function decodeRLE(rle, expectedSize) {
  const out = new Float32Array(expectedSize).fill(20);
  if (!rle) return out;
  let i = 0;
  for (const token of rle.split(',')) {
    if (!token) continue;
    const [v, n] = token.split(':');
    const temperature = Number(v) / 10;
    const count = Number(n);
    for (let k = 0; k < count && i < expectedSize; k++) out[i++] = temperature;
  }
  return out;
}

const afterGrid = computed(() => decodeRLE(props.after?.temp_grid_rle, GRID * GRID));
const activeGrid = computed(() => afterGrid.value);
// Hazard *zones* drawn on the canvas. We union `before` and `after` so the
// thermal/vibration overlays tell the whole story: what the solver found in the
// incoming scene AND what (if anything) survived the correction. A bare
// `after.hazards || before.hazards` would be wrong twice over — an empty array
// is truthy in JS, and the corrector usually resolves vibration, so `after`
// alone would hide the conflicts the agent detected and fixed.
// Dedupe by (type, a_id, b_id) so a hazard present in both states draws once.
const hazards = computed(() => {
  const seen = new Set();
  const merged = [];
  for (const list of [props.after?.hazards, props.before?.hazards]) {
    if (!Array.isArray(list)) continue;
    for (const h of list) {
      const key = `${h?.type}:${h?.a_id}:${h?.b_id ?? ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(h);
    }
  }
  return merged;
});
const peakC = computed(() => props.after?.peak_c ?? props.before?.peak_c ?? 20);

/**
 * Normalized footprint geometry for the paint loop.
 *
 * Priority:
 *   1. `spatialSpec.footprints` — the Muse Image canvas spec (preferred).
 *   2. `finalLayout`            — Muse Wonder's corrected layout.
 *   3. `scene.equipment`        — original scene (legacy / pre-correction).
 *
 * This is what fixes the earlier bug where the canvas painted the *original*
 * layout while the heat overlay came from the corrected one.
 */
const footprints = computed(() => {
  // 0. Scenario editor — the user is editing, so their geometry wins.
  if (props.editable && Array.isArray(props.layout)) {
    return props.layout.map((e) => ({
      id: e.id,
      x: e.x,
      y: e.y,
      w: e.w,
      h: e.h,
      label: e.name || e.id,
    }));
  }

  const spec = props.spatialSpec?.footprints;
  if (Array.isArray(spec) && spec.length) {
    return [...spec]
      .sort((a, b) => (a.z_index ?? 0) - (b.z_index ?? 0))
      .map((f) => ({
        id: f.id,
        x: f.origin[0],
        y: f.origin[1],
        w: f.size[0],
        h: f.size[1],
        label: f.label || f.id,
        // Carry the per-box hazard tags through to the canvas layer. These
        // drive the orange/violet edge stripes (drawHazardStripes).
        hazards: Array.isArray(f.hazards) ? f.hazards : [],
      }));
  }
  const source =
    props.finalLayout?.length > 0
      ? props.finalLayout
      : props.scene?.equipment || [];
  return source.map((e) => ({
    id: e.id,
    x: e.x,
    y: e.y,
    w: e.w,
    h: e.h,
    label: e.id,
  }));
});

function drawBase(context) {
  context.fillStyle = '#04070f';
  context.fillRect(0, 0, WIDTH, HEIGHT);

  context.strokeStyle = 'rgba(148, 163, 184, 0.07)';
  context.lineWidth = 1;
  for (let i = 0; i <= GRID; i += 10) {
    const at = i * CELL + 0.5;
    context.beginPath();
    context.moveTo(at, 0);
    context.lineTo(at, HEIGHT);
    context.stroke();
    context.beginPath();
    context.moveTo(0, at);
    context.lineTo(WIDTH, at);
    context.stroke();
  }
}

function drawThermal(context, grid) {
  for (let y = 0; y < GRID; y++) {
    for (let x = 0; x < GRID; x++) {
      const t = grid[y * GRID + x];
      if (t <= THERMAL_THRESHOLD) continue;
      const alpha = Math.min(0.65, (t - THERMAL_THRESHOLD) / THERMAL_SPAN);
      context.fillStyle = `rgba(249, 115, 22, ${alpha.toFixed(3)})`;
      context.fillRect(x * CELL, y * CELL, CELL, CELL);
    }
  }
}

function drawVibration(context, items) {
  for (const h of items) {
    if (h.type !== 'vibration' || !h.a_center) continue;
    const radius = Math.max(14, (h.measured || 6) * CELL * 0.9);
    const centers = [[h.a_center[0] * CELL, h.a_center[1] * CELL]];
    if (h.b_center) centers.push([h.b_center[0] * CELL, h.b_center[1] * CELL]);

    for (const [cx, cy] of centers) {
      context.beginPath();
      context.arc(cx, cy, radius, 0, Math.PI * 2);
      // Violet (139, 92, 246) matches the per-box vibration stripe below and
      // the legend swatch, so "vibration" reads the same color everywhere.
      context.fillStyle = 'rgba(139, 92, 246, 0.16)';
      context.fill();
      context.strokeStyle = 'rgba(167, 139, 250, 0.85)';
      context.lineWidth = 1.5;
      context.stroke();
    }
  }
}

function drawFootprints(context, items) {
  context.font = '11px ui-monospace, SFMono-Regular, monospace';
  context.textBaseline = 'top';
  for (const item of items) {
    const x = item.x * CELL;
    const y = item.y * CELL;
    const w = item.w * CELL;
    const h = item.h * CELL;

    context.fillStyle = 'rgba(20, 184, 166, 0.14)';
    context.fillRect(x + 1, y + 1, w - 2, h - 2);

    context.strokeStyle = '#14b8a6';
    context.lineWidth = 2;
    context.setLineDash([4, 3]);
    context.strokeRect(x + 1, y + 1, w - 2, h - 2);
    context.setLineDash([]);

    // Per-machine hazard stripes: orange-left for thermal, violet-right for
    // vibration. Kept in sync with the backend PNG renderer (core/png.py) so
    // the canvas thumbnail tells the same story.
    drawHazardStripes(context, item, x, y, w, h);

    context.fillStyle = '#5eead4';
    const label = String(item.label || item.id || '');
    const text = label.length > 20 ? `${label.slice(0, 19)}…` : label;
    context.fillText(text, x + 4, y + 4);
  }
}

function drawHazardStripes(context, item, x, y, w, h) {
  const hazards = Array.isArray(item.hazards) ? item.hazards : [];
  if (!hazards.length) return;
  const stripe = hazards.length > 1 ? 3 : 4;
  const top = y + 1;
  const height = h - 2;
  if (hazards.includes('thermal')) {
    context.fillStyle = 'rgba(249, 115, 22, 0.95)';
    context.fillRect(x + 1, top, stripe, height);
  }
  if (hazards.includes('vibration')) {
    context.fillStyle = 'rgba(139, 92, 246, 0.95)';
    context.fillRect(x + w - 1 - stripe, top, stripe, height);
  }
}

function drawLegend(context) {
  const boxW = 176;
  const boxH = 72;
  const x = WIDTH - boxW - 10;
  const y = 10;

  context.fillStyle = 'rgba(4, 7, 15, 0.85)';
  context.beginPath();
  context.roundRect(x, y, boxW, boxH, 8);
  context.fill();
  context.strokeStyle = 'rgba(148, 163, 184, 0.3)';
  context.lineWidth = 1;
  context.stroke();

  const rows = [
    { color: 'rgba(249, 115, 22, 0.85)', label: 'Thermal (left stripe)' },
    { color: 'rgba(139, 92, 246, 0.85)', label: 'Vibration (right stripe)' },
    { color: '#14b8a6', label: 'Optimized footprint' },
  ];

  context.font = '11px ui-sans-serif, system-ui, sans-serif';
  context.textBaseline = 'middle';
  rows.forEach((row, i) => {
    const ry = y + 18 + i * 18;
    context.fillStyle = row.color;
    context.fillRect(x + 10, ry - 4, 12, 8);
    context.fillStyle = '#cbd5e1';
    context.fillText(row.label, x + 30, ry);
  });
}

function draw() {
  if (!ctx) return;
  drawBase(ctx);
  drawThermal(ctx, activeGrid.value);
  drawVibration(ctx, hazards.value);
  drawFootprints(ctx, footprints.value);
  drawLegend(ctx);
}

function scheduleDraw() {
  if (rafId) cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(draw);
}

onMounted(async () => {
  await nextTick();
  if (!canvasEl.value) return;
  ctx = canvasEl.value.getContext('2d');
  if (ctx) ctx.imageSmoothingEnabled = false;
  scheduleDraw();
});

onBeforeUnmount(() => {
  if (rafId) cancelAnimationFrame(rafId);
  ctx = null;
});

watch(
  () => [props.scene, props.before, props.after, props.spatialSpec, props.finalLayout],
  () => scheduleDraw()
);

defineExpose({ redraw: scheduleDraw });

// ---------------------------------------------------------------------------
// Scenario editor interaction
//
// Two input paths, both snapping to integer grid cells:
//   1. Drop from the catalog sidebar (HTML5 drag-and-drop).
//   2. Pointer-drag an existing footprint already on the canvas.
//
// Both simply emit intent upward; `useScenarioStore` owns collision resolution
// and the debounced physics preview. The canvas never mutates layout itself.
// ---------------------------------------------------------------------------
const overlayEl = ref(null);
const DRAG_MIME = 'application/x-fab-equipment';
let dragState = null;

/** Convert a pointer event into integer grid coordinates. */
function toGrid(event) {
  const rect = overlayEl.value?.getBoundingClientRect();
  if (!rect || !rect.width || !rect.height) return { x: 0, y: 0 };
  const px = ((event.clientX - rect.left) * WIDTH) / rect.width;
  const py = ((event.clientY - rect.top) * HEIGHT) / rect.height;
  return { x: Math.round(px / CELL), y: Math.round(py / CELL) };
}

function onDragOver(event) {
  if (!props.editable) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
}

function onDrop(event) {
  if (!props.editable) return;
  event.preventDefault();
  const raw =
    event.dataTransfer?.getData(DRAG_MIME) ||
    event.dataTransfer?.getData('text/plain');
  if (!raw) return;
  let template;
  try {
    template = JSON.parse(raw);
  } catch {
    return; // not our payload — ignore silently
  }
  if (!template?.id || !template?.w || !template?.h) return;
  const cell = toGrid(event);
  emit('add-equipment', { template, x: cell.x, y: cell.y });
}

function onFootprintDown(event, footprint) {
  if (!props.editable) return;
  event.preventDefault();
  event.stopPropagation();
  const cell = toGrid(event);
  dragState = {
    id: footprint.id,
    offsetX: cell.x - footprint.x,
    offsetY: cell.y - footprint.y,
  };
  emit('select-equipment', footprint.id);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp, { once: true });
}

function onPointerMove(event) {
  if (!dragState) return;
  const cell = toGrid(event);
  emit('move-equipment', {
    id: dragState.id,
    x: cell.x - dragState.offsetX,
    y: cell.y - dragState.offsetY,
  });
}

function onPointerUp() {
  dragState = null;
  window.removeEventListener('pointermove', onPointerMove);
}

function onBackgroundDown(event) {
  if (!props.editable) return;
  if (event.target !== event.currentTarget) return;
  emit('select-equipment', null);
}

function onDeleteSelected() {
  if (!props.editable || !props.selectionId) return;
  emit('remove-equipment', props.selectionId);
}

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onPointerMove);
});
</script>

<template>
  <div
    class="relative inline-block outline-none"
    tabindex="0"
    @keydown.delete.prevent="onDeleteSelected"
    @keydown.backspace.prevent="onDeleteSelected"
  >
    <canvas
      ref="canvasEl"
      :width="WIDTH"
      :height="HEIGHT"
      class="block rounded-lg border border-slate-800 bg-slate-950 shadow-2xl shadow-black/40"
      style="width: 500px; height: 500px"
    />

    <!--
      Interactive layer (scenario editor only).
      Sits above the painted canvas so drag-and-drop and pointer-drag work,
      but below the badges `<slot>`. Footprint rects use a near-transparent
      fill rather than `none`, because SVG will not hit-test a `none` fill.
    -->
    <svg
      v-if="editable"
      ref="overlayEl"
      class="absolute inset-0 h-full w-full touch-none select-none"
      :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
      preserveAspectRatio="none"
      @dragover="onDragOver"
      @drop="onDrop"
      @pointerdown="onBackgroundDown"
    >
      <rect :width="WIDTH" :height="HEIGHT" fill="transparent" />
      <rect
        v-for="f in footprints"
        :key="f.id"
        :x="f.x * CELL"
        :y="f.y * CELL"
        :width="f.w * CELL"
        :height="f.h * CELL"
        :fill="f.id === selectionId ? 'rgba(20, 184, 166, 0.35)' : 'rgba(20, 184, 166, 0.001)'"
        :stroke="f.id === selectionId ? '#5eead4' : 'transparent'"
        stroke-width="1.5"
        class="cursor-move"
        @pointerdown="onFootprintDown($event, f)"
      />
    </svg>

    <slot name="overlay" />

    <div
      class="pointer-events-none absolute bottom-2 left-2 rounded bg-black/70 px-2 py-1 font-mono text-[10px] text-slate-400"
    >
      peak {{ peakC.toFixed(2) }} C · grid 100 x 100 · cell 5px
      <span v-if="editable" class="ml-1 text-teal-400">· edit mode</span>
    </div>
  </div>
</template>
