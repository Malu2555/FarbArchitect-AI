<script setup>
import { computed, onMounted, ref } from 'vue';
import FloorplanCanvas from './components/FloorplanCanvas.vue';
import EventLog from './components/EventLog.vue';
import MetricsPanel from './components/MetricsPanel.vue';
import SceneSummary from './components/SceneSummary.vue';
import SpatialThumbnail from './components/SpatialThumbnail.vue';
import TriggerButton from './components/TriggerButton.vue';
import CatalogSidebar from './components/CatalogSidebar.vue';
import ComputeSuggestionsButton from './components/ComputeSuggestionsButton.vue';
import ConstraintBadge from './components/ConstraintBadge.vue';
import UploadEquipmentBanner from './components/UploadEquipmentBanner.vue';
import { placeEquipment, health } from './api/client';
import { useSimulation } from './composables/useSimulation';
import { useScenarioStore } from './composables/useScenarioStore';

const {
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
  spatialSpec,
  previewPngDataUrl,
  spatialSource,
  noOverlap,
  noClipping,
  canvasProvenance,
  run: runSimulation,
  adopt: adoptSimulationResult,
} = useSimulation();

const {
  equipment: scenarioEquipment,
  selectionId,
  count: scenarioCount,
  addEquipment,
  moveEquipment,
  removeEquipment,
  clearLayout,
  hydrate,
  select,
  liveWarnings,
  previewLoading,
  previewError,
} = useScenarioStore();

const seedInput = ref('');
const backendStatus = ref('checking');
const uploadOpen = ref(false);
const uploadNotice = ref('');

// Scenario editor is opt-in: the seed demo keeps its original canvas priority
// until the user asks to edit or drops a machine from the catalog.
const editRequested = ref(false);
const dropNotice = ref('');

const scenarioActive = computed(
  () => editRequested.value || scenarioCount.value > 0
);

onMounted(async () => {
  try {
    await health();
    backendStatus.value = 'online';
  } catch {
    backendStatus.value = 'offline';
  }
});

const statusText = computed(() => {
  if (loading.value) return 'Running';
  if (error.value) return 'Error';
  if (hasResult.value) return 'Ready';
  return 'Idle';
});

const statusTone = computed(
  () =>
    ({
      Idle: 'bg-slate-800 text-slate-300',
      Ready: 'bg-teal-500/20 text-teal-300',
      Running: 'bg-sky-500/20 text-sky-300',
      Error: 'bg-rose-500/20 text-rose-300',
    }[statusText.value] || 'bg-slate-800 text-slate-300')
);

const spatialTone = computed(() =>
  spatialSource.value === 'MUSE_IMAGE'
    ? 'bg-teal-500/15 text-teal-300'
    : 'bg-amber-500/15 text-amber-300'
);

async function trigger() {
  const raw = seedInput.value.trim();
  const parsed = raw === '' ? null : Number.parseInt(raw, 10);
  const requestedSeed = Number.isInteger(parsed) ? parsed : null;
  try {
    const data = await runSimulation({ seed: requestedSeed });
    // Keep the editor in sync when it is open, so the user can immediately
    // start nudging whatever the agent just produced.
    if (editRequested.value && data?.final_layout) hydrate(data.final_layout);
  } catch {
    /* surfaced through error ref */
  }
}

// ---------------------------------------------------------------------------
// Scenario editor handlers
// ---------------------------------------------------------------------------
function onAddEquipment({ template, x, y }) {
  const outcome = addEquipment(template, x, y);
  editRequested.value = true;
  dropNotice.value = outcome.ok ? '' : `Could not place here (${outcome.reason}).`;
}

function onMoveEquipment({ id, x, y }) {
  moveEquipment(id, x, y);
}

function onRemoveEquipment(id) {
  removeEquipment(id);
}

function toggleEditMode() {
  if (scenarioActive.value) {
    editRequested.value = false;
    clearLayout();
    dropNotice.value = '';
    return;
  }
  // Seed the editor from whatever the canvas is currently showing, so the
  // user starts from the agent's answer instead of an empty floor.
  const source = finalLayout.value?.length
    ? finalLayout.value
    : (scene.value?.equipment ?? []);
  if (source.length) hydrate(source);
  editRequested.value = true;
  dropNotice.value = '';
}

async function computeOnScenario() {
  if (!scenarioCount.value) return;
  try {
    const data = await runSimulation({ equipment: scenarioEquipment.value });
    if (data?.final_layout) hydrate(data.final_layout);
  } catch {
    /* surfaced through error ref */
  }
}

// ---------------------------------------------------------------------------
// Equipment upload handlers
// ---------------------------------------------------------------------------
async function onUploadApply({ upload, strategy }) {
  // Turn parsed drafts into positioned equipment, then open the editor.
  try {
    uploadNotice.value = '';
    const placed = await placeEquipment(upload, { strategy });
    if (placed?.equipment?.length) {
      hydrate(placed.equipment);
      editRequested.value = true;
      dropNotice.value = '';
      uploadNotice.value = `Loaded ${placed.equipment.length} machine(s) from ${upload.source_filename}.`;
      uploadOpen.value = false;
    }
  } catch (err) {
    uploadNotice.value = err?.message || String(err);
  }
}

function onUploadOptimize(result) {
  // `/simulate/upload` already ran the whole chain; adopt its full response so
  // the canvas, metrics, event log and spatial thumbnail all update together.
  if (!result) return;
  adoptSimulationResult(result);
  if (result.final_layout?.length) {
    hydrate(result.final_layout);
    editRequested.value = true;
    uploadNotice.value = 'Optimized your uploaded layout — see the canvas and metrics.';
    uploadOpen.value = false;
  }
}
</script>

<template>
  <div class="flex min-h-screen flex-col bg-[#04070f] text-slate-200">
    <header class="border-b border-slate-800 bg-slate-950/60 px-6 py-3">
      <!--
        Two fixed rows, never swapped. The old single wrapping row pushed the
        action buttons onto a second line whenever the status chips changed
        width (Idle -> Running -> Ready), so the header jumped mid-run.
      -->
      <div class="mx-auto flex max-w-7xl flex-col gap-3">
        <!-- row 1 - brand + run-state chips -->
        <div class="flex flex-wrap items-center gap-3">
          <div
            class="flex h-8 w-8 items-center justify-center rounded-md bg-teal-500/20 font-mono text-xs font-bold text-teal-300"
          >
            fA
          </div>
          <div>
            <h1 class="text-sm font-semibold tracking-wide text-slate-100">fabArchitect AI</h1>
            <p class="text-[10px] uppercase tracking-widest text-slate-500">
              stateless cleanroom simulator
            </p>
          </div>
          <span
            :class="[
              'ml-2 inline-flex min-w-[4.75rem] items-center justify-center whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wider',
              statusTone,
            ]"
          >
            {{ statusText }}
          </span>
          <span
            :class="[
              'whitespace-nowrap rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider',
              backendStatus === 'online'
                ? 'bg-emerald-500/15 text-emerald-300'
                : backendStatus === 'offline'
                  ? 'bg-rose-500/15 text-rose-300'
                  : 'bg-slate-800 text-slate-400',
            ]"
          >
            backend: {{ backendStatus }}
          </span>
          <span
            v-if="seed"
            class="whitespace-nowrap rounded-full bg-slate-800 px-2 py-0.5 font-mono text-[10px] text-slate-300"
          >
            seed #{{ seed }}
          </span>
          <span
            v-if="hasResult"
            :class="[
              'whitespace-nowrap rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider',
              spatialTone,
            ]"
          >
            spatial: {{ spatialSource === 'MUSE_IMAGE' ? 'muse image' : 'local' }}
          </span>
        </div>

        <!-- row 2 - actions, pinned right regardless of chip widths -->
        <div class="flex flex-wrap items-center justify-end gap-3">
          <label class="flex items-center gap-2 text-[11px] text-slate-400">
            <span class="uppercase tracking-wider">seed</span>
            <input
              v-model="seedInput"
              inputmode="numeric"
              placeholder="random"
              class="w-24 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-center font-mono text-slate-200 focus:border-teal-500 focus:outline-none"
            />
          </label>
          <TriggerButton :loading="loading" @run="trigger" />
          <ComputeSuggestionsButton
            :loading="loading"
            :disabled="!scenarioCount"
            :count="scenarioCount"
            @run="computeOnScenario"
          />
          <button
            type="button"
            :aria-expanded="uploadOpen"
            :class="[
              'rounded-lg border px-3 py-2 text-sm font-semibold transition',
              uploadOpen
                ? 'border-teal-500/60 bg-teal-500/15 text-teal-200 hover:bg-teal-500/25'
                : 'border-slate-700 bg-slate-900 text-slate-300 hover:border-teal-600 hover:text-teal-200',
            ]"
            @click="uploadOpen = !uploadOpen"
          >
            📥 Upload equipment
          </button>
          <button
            type="button"
            :class="[
              'rounded-lg border px-3 py-2 text-sm font-semibold transition',
              scenarioActive
                ? 'border-amber-500/60 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20'
                : 'border-slate-700 bg-slate-900 text-slate-300 hover:border-teal-600 hover:text-teal-200',
            ]"
            @click="toggleEditMode"
          >
            {{ scenarioActive ? '✕ Exit edit' : '✎ Edit layout' }}
          </button>
        </div>
      </div>
    </header>

    <UploadEquipmentBanner
      v-if="uploadOpen"
      @apply="onUploadApply"
      @optimize="onUploadOptimize"
      @close="uploadOpen = false"
    />

    <p
      v-if="uploadNotice"
      class="mx-auto w-full max-w-7xl px-6 pb-1 text-[11px] text-teal-300"
    >
      {{ uploadNotice }}
    </p>

    <main
      class="mx-auto grid w-full max-w-7xl flex-1 gap-4 p-6 lg:grid-cols-[18rem_minmax(0,1fr)_22rem]"
    >
      <aside class="flex min-h-0 flex-col gap-4">
        <MetricsPanel
          :before="before"
          :after="after"
          :delta-t="deltaT"
          :providers-used="providersUsed"
          :elapsed-ms="elapsedMs"
        />
        <SceneSummary :scene="scene" :layout="finalLayout" />
        <SpatialThumbnail
          :data-url="previewPngDataUrl"
          :source="spatialSource"
          :no-overlap="noOverlap"
          :no-clipping="noClipping"
          :canvas-px="canvasProvenance?.canvas_px || [500, 500]"
          :grid-size="canvasProvenance?.grid_size || 100"
        />
      </aside>

      <section class="flex min-h-0 flex-col items-center justify-center gap-3">
        <FloorplanCanvas
          :scene="scene"
          :before="before"
          :after="after"
          :spatial-spec="spatialSpec"
          :final-layout="finalLayout"
          :layout="scenarioActive ? scenarioEquipment : null"
          :editable="scenarioActive"
          :selection-id="selectionId"
          @add-equipment="onAddEquipment"
          @move-equipment="onMoveEquipment"
          @remove-equipment="onRemoveEquipment"
          @select-equipment="select"
        >
          <template #overlay>
            <ConstraintBadge
              v-if="scenarioActive"
              :synopsis="liveWarnings"
              :loading="previewLoading"
              :error="previewError || dropNotice"
              :count="scenarioCount"
            />
          </template>
        </FloorplanCanvas>

        <p
          v-if="scenarioActive"
          class="max-w-[500px] text-center text-[11px] text-slate-500"
        >
          <span class="font-mono text-teal-300">{{ scenarioCount }}</span> machine{{
            scenarioCount === 1 ? '' : 's'
          }}
          placed · drag to reposition · <span class="font-mono">Del</span> removes the
          selected one · physics preview is live
        </p>

        <p
          v-if="error"
          class="w-full max-w-[500px] rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
        >
          {{ error }}
        </p>
        <p
          v-if="!hasResult && !loading"
          class="max-w-[500px] text-center text-xs text-slate-500"
        >
          Press
          <span class="font-mono text-slate-300">Run simulation</span>
          to generate a synthetic cleanroom, solve the 2D FDM thermal field, and run the
          agentic self-correction loop.
        </p>
      </section>

      <aside class="flex min-h-0 flex-col gap-4">
        <!--
          Palette sits next to the canvas (standard CAD placement) so the drag
          path is short and perpendicular. Both panels share the column
          proportionally via flex-[3]/flex-[2] + basis-0, and each scrolls
          internally — neither can stretch the column past the canvas height.
        -->
        <CatalogSidebar class="min-h-0 flex-[3] basis-0" />
        <EventLog :corrections="corrections" class="min-h-0 flex-[2] basis-0" />
      </aside>
    </main>

    <footer
      class="border-t border-slate-800 px-6 py-2 text-center text-[10px] uppercase tracking-widest text-slate-600"
    >
      Vue 3 · Vite 5 · Tailwind 3.4 · Django Ninja · Modal · FDM 100×100 · ≤3 correction turns
    </footer>
  </div>
</template>
