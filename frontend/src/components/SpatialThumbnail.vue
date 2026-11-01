<script setup>
/**
 * Stage 2 preview tile — the base64 PNG returned by the Muse Image spatial
 * renderer (or by the local fallback rasteriser). Sits beside the live
 * FloorplanCanvas so reviewers can see both views of the same geometry.
 */
import { computed } from 'vue';

const props = defineProps({
  dataUrl: { type: String, default: '' },
  source: { type: String, default: 'FALLBACK' },
  noOverlap: { type: Boolean, default: true },
  noClipping: { type: Boolean, default: true },
  canvasPx: { type: Array, default: () => [500, 500] },
  gridSize: { type: Number, default: 100 },
});

const isMuse = computed(() => props.source === 'MUSE_IMAGE');

const badgeClass = computed(() =>
  isMuse.value
    ? 'bg-teal-500/20 text-teal-300'
    : 'bg-amber-500/20 text-amber-300'
);

const canvasLabel = computed(() => {
  const [w, h] = props.canvasPx;
  return `${w}×${h}`;
});
</script>

<template>
  <section class="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
    <header class="mb-2 flex items-center justify-between">
      <h2 class="text-xs font-semibold uppercase tracking-widest text-slate-500">
        Spatial preview
      </h2>
      <span
        :class="[
          'rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider',
          badgeClass,
        ]"
      >
        {{ isMuse ? 'Muse Image' : 'Local' }}
      </span>
    </header>

    <div
      v-if="dataUrl"
      class="overflow-hidden rounded-lg border border-slate-800 bg-black"
    >
      <img
        :src="dataUrl"
        alt="Muse Image spatial preview"
        class="block h-auto w-full"
        width="500"
        height="500"
      />
    </div>
    <div
      v-else
      class="flex aspect-square items-center justify-center rounded-lg border border-dashed border-slate-800 text-[11px] text-slate-600"
    >
      No preview yet
    </div>

    <dl class="mt-2 grid grid-cols-2 gap-1 font-mono text-[10px]">
      <div
        class="flex items-center justify-between rounded border border-slate-800 bg-slate-950/40 px-2 py-1"
      >
        <dt class="text-slate-500">overlaps</dt>
        <dd :class="noOverlap ? 'text-emerald-400' : 'text-rose-400'">
          {{ noOverlap ? 'none' : 'detected' }}
        </dd>
      </div>
      <div
        class="flex items-center justify-between rounded border border-slate-800 bg-slate-950/40 px-2 py-1"
      >
        <dt class="text-slate-500">clipping</dt>
        <dd :class="noClipping ? 'text-emerald-400' : 'text-rose-400'">
          {{ noClipping ? 'none' : 'detected' }}
        </dd>
      </div>
      <div
        class="col-span-2 rounded border border-slate-800 bg-slate-950/40 px-2 py-1 text-slate-500"
      >
        {{ canvasLabel }} · grid {{ gridSize }}×{{ gridSize }}
      </div>
    </dl>
  </section>
</template>
