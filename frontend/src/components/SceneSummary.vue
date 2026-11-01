<script setup>
import { computed } from 'vue';

const props = defineProps({
  scene: { type: Object, default: null },
  layout: { type: Array, default: () => [] },
});

const layoutIndex = computed(() => {
  const map = new Map();
  for (const item of props.layout) map.set(item.id, item);
  return map;
});

const rows = computed(() =>
  (props.scene?.equipment || []).map((item) => {
    const moved = layoutIndex.value.get(item.id);
    const to = moved ? { x: moved.x, y: moved.y } : { x: item.x, y: item.y };
    return {
      id: item.id,
      name: item.name,
      heat: item.heat_watts,
      vibration: item.vibration_db,
      from: `${item.x},${item.y}`,
      to: `${to.x},${to.y}`,
      moved: moved && (moved.x !== item.x || moved.y !== item.y),
    };
  })
);
</script>

<template>
  <section class="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
    <header class="mb-2 flex items-center justify-between">
      <h2 class="text-xs font-semibold uppercase tracking-widest text-slate-500">
        Scene summary
      </h2>
      <span class="font-mono text-[10px] text-slate-500">{{ rows.length }} machines</span>
    </header>

    <div class="max-h-72 overflow-auto rounded border border-slate-800">
      <table class="w-full text-left text-xs">
        <thead class="sticky top-0 bg-slate-950/90 text-[10px] uppercase tracking-wider text-slate-500">
          <tr>
            <th class="px-2 py-1.5">ID</th>
            <th class="px-2 py-1.5">Heat</th>
            <th class="px-2 py-1.5">dB</th>
            <th class="px-2 py-1.5">From → To</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-800/70">
          <tr
            v-for="row in rows"
            :key="row.id"
            :class="row.moved ? 'bg-emerald-500/5' : ''"
          >
            <td class="px-2 py-1.5 font-mono text-slate-300">{{ row.id }}</td>
            <td class="px-2 py-1.5 font-mono text-slate-400">
              {{ Number(row.heat).toFixed(0) }}W
            </td>
            <td class="px-2 py-1.5 font-mono text-slate-400">
              {{ Number(row.vibration).toFixed(0) }}
            </td>
            <td class="px-2 py-1.5 font-mono text-slate-400">
              <span>{{ row.from }}</span>
              <span v-if="row.moved" class="text-emerald-400"> → {{ row.to }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
