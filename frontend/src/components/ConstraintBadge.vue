<script setup>
/**
 * Live physics verdict for the user's hand-built layout.
 *
 * Fed by `POST /api/v1/simulate/synopsis`, which runs the FDM + vibration
 * pipeline and **never** calls an LLM. That is what makes it safe to fire on
 * every drag settle (debounced to 250 ms in `useScenarioStore`).
 *
 * Colour encodes the worst finding, not a score: a layout is only "clean" when
 * the solver says so — the same authority the corrector's trust-but-verify gate
 * uses for model proposals.
 */
import { computed } from 'vue';

const props = defineProps({
  synopsis: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  count: { type: Number, default: 0 },
});

const peak = computed(() =>
  typeof props.synopsis?.peak_c === 'number' ? props.synopsis.peak_c : null
);

const hazardCount = computed(() => props.synopsis?.hazards?.length ?? 0);

const codes = computed(() => props.synopsis?.error_codes ?? []);

const tone = computed(() => {
  if (props.error) return 'border-rose-500/50 bg-rose-500/10 text-rose-200';
  if (!props.count) return 'border-slate-700 bg-slate-900/80 text-slate-400';
  if (props.loading) return 'border-sky-500/40 bg-sky-500/10 text-sky-200';
  if (!codes.value.length) return 'border-emerald-500/50 bg-emerald-500/10 text-emerald-200';
  const hasThermal = codes.value.includes('E_TEMP_PEAK');
  const hasVibration = codes.value.includes('E_VIBRATION_CONFLICT');
  if (hasThermal && hasVibration)
    return 'border-fuchsia-500/50 bg-fuchsia-500/10 text-fuchsia-200';
  if (hasThermal) return 'border-rose-500/50 bg-rose-500/10 text-rose-200';
  if (hasVibration) return 'border-violet-500/50 bg-violet-500/10 text-violet-200';
  return 'border-amber-500/50 bg-amber-500/10 text-amber-200';
});

const headline = computed(() => {
  if (props.error) return 'preview unavailable';
  if (!props.count) return 'empty floor';
  if (props.loading && peak.value === null) return 'solving…';
  return 'live physics';
});
</script>

<template>
  <div
    :class="[
      'pointer-events-none absolute right-2 top-2 rounded-lg border px-2.5 py-1.5 font-mono text-[10px] backdrop-blur-sm',
      tone,
    ]"
  >
    <div class="flex items-center gap-2">
      <span class="uppercase tracking-widest opacity-70">{{ headline }}</span>
      <span
        v-if="loading"
        class="h-2 w-2 animate-pulse rounded-full bg-current opacity-70"
      ></span>
    </div>

    <div v-if="peak !== null" class="mt-0.5 flex items-center gap-2">
      <span>
        peak
        <strong class="font-bold">{{ peak.toFixed(2) }}</strong>
        C
      </span>
      <span class="opacity-40">·</span>
      <span>{{ hazardCount }} hazard{{ hazardCount === 1 ? '' : 's' }}</span>
    </div>

    <div v-if="codes.length" class="mt-1 flex flex-wrap gap-1">
      <span
        v-for="code in codes"
        :key="code"
        class="rounded bg-black/30 px-1 py-0.5 text-[9px] tracking-wide"
      >
        {{ code }}
      </span>
    </div>

    <div v-if="error" class="mt-1 max-w-[14rem] truncate text-[9px] opacity-80">
      {{ error }}
    </div>
  </div>
</template>
