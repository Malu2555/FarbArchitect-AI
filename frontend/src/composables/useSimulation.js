/**
 * Reactive simulation state — the single source of truth for the dashboard.
 *
 * Deliberately uses NO persistence (localStorage / IndexedDB / cookies).
 * Every reload starts from a clean slate; replays come from the `seed` echoed
 * in the response so users can share a reproducible experiment.
 */

import { computed, ref } from 'vue';
import { simulate } from '../api/client';

export function useSimulation() {
  const result = ref(null);
  const loading = ref(false);
  const error = ref(null);
  const startedAt = ref(null);
  const finishedAt = ref(null);

  const hasResult = computed(() => !!result.value);
  const before = computed(() => result.value?.before ?? null);
  const after = computed(() => result.value?.after ?? null);
  const scene = computed(() => result.value?.scene ?? null);
  const corrections = computed(() => result.value?.corrections ?? []);
  const finalLayout = computed(() => result.value?.final_layout ?? []);
  const providersUsed = computed(() => result.value?.providers_used ?? []);
  const deltaT = computed(() => result.value?.delta_t ?? 0);
  const seed = computed(() => result.value?.seed ?? null);

  // --- Stage 2: Muse Image spatial renderer output -------------------------
  const spatial = computed(() => result.value?.spatial ?? null);
  const spatialSpec = computed(() => spatial.value?.spatial_spec ?? null);
  const previewPngDataUrl = computed(
    () => spatial.value?.preview_png_data_url ?? ''
  );
  const canvasProvenance = computed(() => spatialSpec.value?.canvas ?? null);
  const spatialSource = computed(
    () =>
      result.value?.spatial_source ??
      canvasProvenance.value?.source ??
      'FALLBACK'
  );
  const noOverlap = computed(() => canvasProvenance.value?.no_overlap ?? true);
  const noClipping = computed(() => canvasProvenance.value?.no_clipping ?? true);

  const elapsedMs = computed(() => {
    if (!startedAt.value || !finishedAt.value) return 0;
    return finishedAt.value - startedAt.value;
  });

  async function run({ seed: requestedSeed = null, equipment = null } = {}) {
    if (loading.value) return null;
    loading.value = true;
    error.value = null;
    startedAt.value = performance.now();
    finishedAt.value = null;

    try {
      const data = await simulate({ seed: requestedSeed, equipment });
      result.value = data;
      finishedAt.value = performance.now();
      return data;
    } catch (err) {
      error.value = err?.message || String(err);
      finishedAt.value = performance.now();
      throw err;
    } finally {
      loading.value = false;
    }
  }

  function reset() {
    result.value = null;
    error.value = null;
    startedAt.value = null;
    finishedAt.value = null;
  }

  return {
    result,
    loading,
    error,
    hasResult,
    before,
    after,
    scene,
    corrections,
    finalLayout,
    providersUsed,
    deltaT,
    elapsedMs,
    seed,
    // Stage 2 spatial renderer
    spatial,
    spatialSpec,
    previewPngDataUrl,
    canvasProvenance,
    spatialSource,
    noOverlap,
    noClipping,
    run,
    reset,
  };
}
