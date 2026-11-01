<script setup>
/**
 * Secondary CTA for the scenario editor: run the agent on the user's layout.
 *
 * Distinct from `TriggerButton` on purpose. That one asks the generator for a
 * synthetic scene; this one sends the machines the user placed. Both hit the
 * same `/api/v1/simulate`, so the corrector, its trust-but-verify gate and the
 * deterministic fallback behave identically either way.
 */
const props = defineProps({
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  count: { type: Number, default: 0 },
});

const emit = defineEmits(['run']);

function handleClick() {
  if (!props.loading && !props.disabled) emit('run');
}
</script>

<template>
  <button
    type="button"
    :disabled="loading || disabled"
    :title="
      disabled && !count
        ? 'Place at least one machine first'
        : 'Run the corrector on the layout you built'
    "
    class="group inline-flex items-center gap-2 rounded-lg border border-teal-600/60 bg-teal-500/10 px-3 py-2 text-sm font-semibold text-teal-200 transition hover:border-teal-400 hover:bg-teal-500/20 disabled:cursor-not-allowed disabled:border-slate-700 disabled:bg-slate-800/60 disabled:text-slate-500"
    @click="handleClick"
  >
    <span
      v-if="loading"
      class="h-3 w-3 animate-spin rounded-full border-2 border-teal-200 border-t-transparent"
    ></span>
    <span v-else class="text-base leading-none">🤖</span>
    {{ loading ? 'Correcting…' : 'Compute suggestions' }}
  </button>
</template>
