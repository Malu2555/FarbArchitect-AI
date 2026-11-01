<script setup>
import { computed, nextTick, ref, watch } from 'vue';

const props = defineProps({
  corrections: { type: Array, default: () => [] },
});

const scroller = ref(null);

watch(
  () => props.corrections,
  async () => {
    await nextTick();
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
  },
  { deep: true }
);

const PHASE_TONE = {
  evaluate: 'border-l-sky-400',
  correct: 'border-l-emerald-400',
  verify: 'border-l-violet-400',
  error: 'border-l-amber-400',
};

const PHASE_TEXT = {
  evaluate: 'text-sky-300',
  correct: 'text-emerald-300',
  verify: 'text-violet-300',
  error: 'text-amber-300',
};

function tone(phase) {
  return PHASE_TONE[phase] || 'border-l-slate-600';
}
function phaseText(phase) {
  return PHASE_TEXT[phase] || 'text-slate-300';
}

const entries = computed(() =>
  props.corrections.map((c, i) => ({ ...c, _key: `${c.turn}-${c.phase}-${i}` }))
);
</script>

<template>
  <section class="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40 p-4">
    <header class="mb-2 flex shrink-0 items-center justify-between">
      <h2 class="text-xs font-semibold uppercase tracking-widest text-slate-500">
        Agent event log
      </h2>
      <span class="font-mono text-[10px] text-slate-500">{{ corrections.length }} entries</span>
    </header>

    <div ref="scroller" class="min-h-0 flex-1 overflow-y-auto pr-1">
      <p v-if="!corrections.length" class="text-sm text-slate-600">
        No simulation yet.
      </p>

      <ol v-else class="space-y-2">
        <li
          v-for="entry in entries"
          :key="entry._key"
          :class="[
            'rounded-md border-l-4 bg-slate-950/70 px-3 py-2',
            tone(entry.phase),
          ]"
        >
          <div class="flex items-center justify-between text-[10px] uppercase tracking-wider">
            <span :class="['font-mono', phaseText(entry.phase)]">
              turn {{ entry.turn }} · {{ entry.phase }}
            </span>
            <span v-if="entry.peak_c != null" class="font-mono text-slate-400">
              {{ Number(entry.peak_c).toFixed(2) }}°C
            </span>
          </div>
          <p class="mt-1 break-words text-xs text-slate-300">{{ entry.summary }}</p>
          <p v-if="entry.error_codes?.length" class="mt-1 flex flex-wrap gap-1">
            <span
              v-for="code in entry.error_codes"
              :key="code"
              class="rounded-full bg-rose-950/60 px-2 py-0.5 font-mono text-[10px] text-rose-300"
            >
              {{ code }}
            </span>
          </p>
        </li>
      </ol>
    </div>
  </section>
</template>
