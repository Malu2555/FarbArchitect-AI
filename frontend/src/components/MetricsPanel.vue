<script setup>
import { computed } from 'vue';

const props = defineProps({
  before: { type: Object, default: null },
  after: { type: Object, default: null },
  deltaT: { type: Number, default: 0 },
  providersUsed: { type: Array, default: () => [] },
  elapsedMs: { type: Number, default: 0 },
});

const beforePeak = computed(() => props.before?.peak_c ?? null);
const afterPeak = computed(() => props.after?.peak_c ?? null);
const beforeHazards = computed(() => props.before?.hazards?.length ?? 0);
const afterHazards = computed(() => props.after?.hazards?.length ?? 0);

const improvement = computed(() => {
  if (beforePeak.value == null || afterPeak.value == null) return 0;
  if (beforePeak.value === 0) return 0;
  return ((beforePeak.value - afterPeak.value) / beforePeak.value) * 100;
});

function fmt(value) {
  return value == null ? '—' : Number(value).toFixed(2);
}
</script>

<template>
  <section class="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
    <h2 class="text-xs font-semibold uppercase tracking-widest text-slate-500">
      Thermal telemetry
    </h2>

    <div class="grid grid-cols-2 gap-3">
      <div class="rounded-lg border border-rose-500/20 bg-rose-500/5 p-3">
        <p class="text-[10px] uppercase tracking-wider text-rose-300/70">Before peak</p>
        <p class="mt-1 font-mono text-2xl font-semibold text-rose-300">
          {{ fmt(beforePeak) }}<span class="text-sm text-slate-500">°C</span>
        </p>
        <p class="text-[10px] text-slate-500">{{ beforeHazards }} hazards</p>
      </div>
      <div class="rounded-lg border border-teal-500/30 bg-teal-500/5 p-3">
        <p class="text-[10px] uppercase tracking-wider text-teal-300/70">After peak</p>
        <p class="mt-1 font-mono text-2xl font-semibold text-teal-200">
          {{ fmt(afterPeak) }}<span class="text-sm text-slate-500">°C</span>
        </p>
        <p class="text-[10px] text-slate-500">{{ afterHazards }} hazards</p>
      </div>
    </div>

    <div class="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/60 p-3">
      <div>
        <p class="text-[10px] uppercase tracking-wider text-slate-500">Delta T</p>
        <p class="font-mono text-lg font-semibold text-emerald-400">
          -{{ Number(deltaT || 0).toFixed(2) }}°C
        </p>
      </div>
      <div class="text-right">
        <p class="text-[10px] uppercase tracking-wider text-slate-500">Improvement</p>
        <p class="font-mono text-lg font-semibold text-emerald-300">
          {{ improvement.toFixed(1) }}%
        </p>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-2 text-[11px]">
      <div class="rounded border border-slate-800 bg-slate-950/40 px-2 py-1.5">
        <span class="text-slate-500">Providers</span>
        <p class="truncate font-mono text-slate-200">
          {{ providersUsed.length ? providersUsed.join(', ') : 'offline/fallback' }}
        </p>
      </div>
      <div class="rounded border border-slate-800 bg-slate-950/40 px-2 py-1.5">
        <span class="text-slate-500">Elapsed</span>
        <p class="font-mono text-slate-200">
          {{ elapsedMs ? `${Math.round(elapsedMs)} ms` : '—' }}
        </p>
      </div>
    </div>
  </section>
</template>
