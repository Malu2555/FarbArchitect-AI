<script setup>
/**
 * One draggable machine type in the scenario editor catalog.
 *
 * Carries the canonical template (footprint + heat + vibration) as a JSON
 * drag payload. The canvas drop handler reads MIME `application/x-fab-equipment`,
 * so the numbers a dropped machine gets are the exact ones the synthetic
 * generator would have used for the same type.
 */
const props = defineProps({
  item: { type: Object, required: true },
  armed: { type: Boolean, default: false },
});

const emit = defineEmits(['arm']);

const DRAG_MIME = 'application/x-fab-equipment';

function onDragStart(event) {
  if (!event.dataTransfer) return;
  event.dataTransfer.effectAllowed = 'copy';
  event.dataTransfer.setData(DRAG_MIME, JSON.stringify(props.item));
  event.dataTransfer.setData('text/plain', props.item.name);
}

function onClick() {
  emit('arm', props.item);
}
</script>

<template>
  <button
    type="button"
    draggable="true"
    :aria-pressed="armed"
    :class="[
      'group flex w-full cursor-grab items-center gap-2 rounded-lg border px-2 py-1.5 text-left transition active:cursor-grabbing',
      armed
        ? 'border-teal-400 bg-teal-500/15'
        : 'border-slate-800 bg-slate-900/60 hover:border-teal-600/60 hover:bg-slate-900',
    ]"
    :title="`Drag onto the canvas, or click then click the canvas`"
    @dragstart="onDragStart"
    @click="onClick"
  >
    <span
      class="flex h-7 w-7 shrink-0 items-center justify-center rounded bg-slate-800 font-mono text-[10px] font-bold text-teal-300 group-hover:bg-slate-700"
    >
      {{ item.glyph || item.id.slice(0, 2) }}
    </span>

    <span class="min-w-0 flex-1">
      <span class="block truncate text-[11px] font-medium text-slate-200">
        {{ item.name }}
      </span>
      <span class="block font-mono text-[10px] text-slate-500">
        {{ item.w }}×{{ item.h }} · {{ item.heat_watts }}W · {{ item.vibration_db }}dB
      </span>
    </span>
  </button>
</template>
