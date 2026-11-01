/**
 * Shared scenario-editor state: the user's hand-built cleanroom layout.
 *
 * Module-level refs (not per-call like `useSimulation`) because App.vue, the
 * catalog sidebar, the canvas and the constraint badge all read the same
 * layout. Pinia would do this too, but the project intentionally ships zero
 * extra runtime dependencies.
 *
 * Persistence: **none**. No localStorage, no IndexedDB, no cookies. Everything
 * lives in memory and disappears on reload, preserving the project's
 * "stateless" invariant. A reproducible layout is re-derived from the seed.
 *
 * The live preview is debounced and single-flight: rapid dragging produces at
 * most one in-flight `/simulate/synopsis` request, and a newer edit aborts the
 * older request rather than queueing behind it.
 */

import { computed, ref } from 'vue';
import { equipmentCatalog, synopsis } from '../api/client';

export const GRID = 100;
const PREVIEW_DEBOUNCE_MS = 250;

// --- catalog -----------------------------------------------------------------
const catalog = ref([]);
const catalogLoading = ref(false);
const catalogError = ref(null);

// --- layout ------------------------------------------------------------------
const equipment = ref([]);
const selectionId = ref(null);

// --- live physics preview ----------------------------------------------------
const liveWarnings = ref(null);
const previewLoading = ref(false);
const previewError = ref(null);

let idCounter = 0;
let debounceTimer = 0;
let inflight = null;

// -----------------------------------------------------------------------------
// Geometry helpers
// -----------------------------------------------------------------------------
function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function overlaps(a, b) {
  return !(
    a.x + a.w <= b.x ||
    b.x + b.w <= a.x ||
    a.y + a.h <= b.y ||
    b.y + b.h <= a.y
  );
}

function collides(candidate, others) {
  return others.some((other) => other.id !== candidate.id && overlaps(candidate, other));
}

/**
 * Find a legal position near (x, y): the requested cell first, then a spiral of
 * nearby cells. Returns `null` when the layout is genuinely full.
 */
function settle(candidate, others) {
  const maxX = GRID - candidate.w;
  const maxY = GRID - candidate.h;
  if (maxX < 0 || maxY < 0) return null;

  const baseX = clamp(candidate.x, 0, maxX);
  const baseY = clamp(candidate.y, 0, maxY);
  const direct = { ...candidate, x: baseX, y: baseY };
  if (!collides(direct, others)) return direct;

  for (let radius = 1; radius <= 12; radius += 1) {
    const offsets = [];
    for (let dx = -radius; dx <= radius; dx += 1) {
      offsets.push([dx, -radius], [dx, radius]);
    }
    for (let dy = -radius + 1; dy < radius; dy += 1) {
      offsets.push([-radius, dy], [radius, dy]);
    }
    for (const [dx, dy] of offsets) {
      const tried = {
        ...candidate,
        x: clamp(baseX + dx, 0, maxX),
        y: clamp(baseY + dy, 0, maxY),
      };
      if (!collides(tried, others)) return tried;
    }
  }
  return null;
}

function nextId(prefix) {
  idCounter += 1;
  return `${prefix}-${String(idCounter).padStart(2, '0')}`;
}

// -----------------------------------------------------------------------------
// Catalog
// -----------------------------------------------------------------------------
async function loadCatalog() {
  if (catalog.value.length || catalogLoading.value) return catalog.value;
  catalogLoading.value = true;
  catalogError.value = null;
  try {
    catalog.value = await equipmentCatalog();
    return catalog.value;
  } catch (err) {
    catalogError.value = err?.message || String(err);
    return [];
  } finally {
    catalogLoading.value = false;
  }
}

// -----------------------------------------------------------------------------
// Layout mutations
// -----------------------------------------------------------------------------
function addEquipment(template, x, y) {
  if (!template) return { ok: false, reason: 'E_NO_TEMPLATE' };
  const candidate = {
    id: nextId(template.id),
    name: template.name,
    x: Math.round(x),
    y: Math.round(y),
    w: template.w,
    h: template.h,
    heat_watts: template.heat_watts,
    vibration_db: template.vibration_db,
  };
  const placed = settle(candidate, equipment.value);
  if (!placed) return { ok: false, reason: 'E_NO_SPACE' };

  equipment.value = [...equipment.value, placed];
  selectionId.value = placed.id;
  schedulePreview();
  return { ok: true, item: placed };
}

function moveEquipment(id, x, y) {
  const index = equipment.value.findIndex((item) => item.id === id);
  if (index === -1) return { ok: false, reason: 'E_UNKNOWN_ID' };

  const current = equipment.value[index];
  const candidate = { ...current, x: Math.round(x), y: Math.round(y) };
  const others = equipment.value.filter((item) => item.id !== id);
  const placed = settle(candidate, others);
  if (!placed) return { ok: false, reason: 'E_NO_SPACE' };

  const next = [...equipment.value];
  next[index] = placed;
  equipment.value = next;
  schedulePreview();
  return { ok: true, item: placed };
}

function removeEquipment(id) {
  const before = equipment.value.length;
  equipment.value = equipment.value.filter((item) => item.id !== id);
  if (equipment.value.length === before) return false;
  if (selectionId.value === id) selectionId.value = null;
  schedulePreview();
  return true;
}

function clearLayout() {
  equipment.value = [];
  selectionId.value = null;
  liveWarnings.value = null;
  previewError.value = null;
}

/** Adopt an authoritative layout (e.g. the corrector's `final_layout`). */
function hydrate(items) {
  if (!Array.isArray(items)) return;
  equipment.value = items.map((item) => ({
    id: item.id,
    name: item.name ?? item.id,
    x: item.x,
    y: item.y,
    w: item.w,
    h: item.h,
    heat_watts: item.heat_watts,
    vibration_db: item.vibration_db,
  }));
  if (!equipment.value.some((item) => item.id === selectionId.value)) {
    selectionId.value = null;
  }
  schedulePreview();
}

function select(id) {
  selectionId.value = id;
}

const selectedItem = computed(
  () => equipment.value.find((item) => item.id === selectionId.value) ?? null
);

const count = computed(() => equipment.value.length);

const violationCodes = computed(() => liveWarnings.value?.error_codes ?? []);

const isClean = computed(
  () => !previewLoading.value && violationCodes.value.length === 0
);

// -----------------------------------------------------------------------------
// Debounced, single-flight physics preview
// -----------------------------------------------------------------------------
function schedulePreview(delay = PREVIEW_DEBOUNCE_MS) {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    debounceTimer = 0;
    void runPreview();
  }, delay);
}

async function runPreview() {
  if (inflight) inflight.abort();
  const controller = new AbortController();
  inflight = controller;

  const snapshot = equipment.value.map((item) => ({ ...item }));
  if (!snapshot.length) {
    liveWarnings.value = null;
    previewLoading.value = false;
    if (inflight === controller) inflight = null;
    return;
  }

  previewLoading.value = true;
  previewError.value = null;
  try {
    const data = await synopsis({ equipment: snapshot, signal: controller.signal });
    if (inflight === controller) liveWarnings.value = data;
  } catch (err) {
    if (inflight !== controller) return; // superseded — ignore
    if (err?.name === 'AbortError') return;
    previewError.value = err?.message || String(err);
  } finally {
    if (inflight === controller) {
      inflight = null;
      previewLoading.value = false;
    }
  }
}

// -----------------------------------------------------------------------------
// Public singleton
// -----------------------------------------------------------------------------
export function useScenarioStore() {
  return {
    GRID,
    // catalog
    catalog,
    catalogLoading,
    catalogError,
    loadCatalog,
    // layout
    equipment,
    selectionId,
    selectedItem,
    count,
    addEquipment,
    moveEquipment,
    removeEquipment,
    clearLayout,
    hydrate,
    select,
    // live physics preview
    liveWarnings,
    previewLoading,
    previewError,
    violationCodes,
    isClean,
    schedulePreview,
    runPreview,
  };
}
