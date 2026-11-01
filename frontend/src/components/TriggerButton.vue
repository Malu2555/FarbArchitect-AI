<script setup>
const props = defineProps({
  loading: { type: Boolean, default: false },
});

const emit = defineEmits(['run']);

function handleClick() {
  if (!props.loading) emit('run');
}
</script>

<template>
  <button
    type="button"
    :disabled="loading"
    class="group inline-flex items-center gap-2 rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-teal-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
    @click="handleClick"
  >
    <span
      v-if="loading"
      class="h-3 w-3 animate-spin rounded-full border-2 border-slate-950 border-t-transparent"
    ></span>
    <span v-else class="text-base leading-none">▸</span>
    {{ loading ? 'Simulating…' : 'Run simulation' }}
  </button>
</template>
