<script setup>
/**
 * Equipment catalog for the scenario editor.
 *
 * Rows come from `GET /api/v1/equipment/catalog`, which is generated from the
 * same `core/synthetic_gen.CATALOG` tuple that seeds the synthetic generator.
 * Keeping it server-side means a dragged-in machine and a generator-placed one
 * are guaranteed identical.
 */
import { onMounted } from 'vue';
import CatalogEquipmentCard from './CatalogEquipmentCard.vue';
import { useScenarioStore } from '../composables/useScenarioStore';

defineProps({
  armedId: { type: String, default: null },
});

const emit = defineEmits(['arm']);

const { catalog, catalogLoading, catalogError, loadCatalog, count } =
  useScenarioStore();

onMounted(() => {
  void loadCatalog();
});
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60">
    <div class="flex shrink-0 items-center justify-between border-b border-slate-800 px-3 py-2">
      <h2 class="text-[10px] font-semibold uppercase tracking-widest text-slate-400">
        Equipment catalog
      </h2>
      <span class="font-mono text-[10px] text-slate-600">{{ count }} placed</span>
    </div>

    <div class="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
      <p v-if="catalogLoading" class="px-1 py-2 text-[11px] text-slate-500">
        loading catalog…
      </p>
      <p v-else-if="catalogError" class="px-1 py-2 text-[11px] text-rose-300">
        {{ catalogError }}
      </p>
      <template v-else>
        <CatalogEquipmentCard
          v-for="item in catalog"
          :key="item.id"
          :item="item"
          :armed="armedId === item.id"
          @arm="emit('arm', $event)"
        />
      </template>
    </div>

    <p class="shrink-0 border-t border-slate-800 px-3 py-2 text-[10px] leading-relaxed text-slate-600">
      Drag onto the canvas — or tap a card, then tap the floor.
    </p>
  </div>
</template>
