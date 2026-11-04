<script setup>
/**
 * User equipment upload banner (Option B: header button + full-width banner).
 *
 * Flow:
 *   idle -> dropping a file (or picking one) -> parsing -> parsed | error
 *
 * On a successful parse it renders a compact summary table and emits `apply`
 * with the parsed upload; the parent (App.vue) hydrates the scenario store and
 * flips into edit mode. It never mutates global state itself, so it stays a
 * pure, testable leaf component.
 *
 * Deliberately additive: it touches none of the existing simulation flow. The
 * "Optimize uploaded list" path calls the one-shot `/simulate/upload` endpoint
 * and hands the full SimulateResponse back to the parent to render.
 */
import { computed, ref } from 'vue';
import { parseEquipmentUpload, simulateUpload } from '../api/client';

const emit = defineEmits(['apply', 'optimize', 'close']);

const fileInput = ref(null);
const phase = ref('idle'); // idle | parsing | parsed | optimizing | error
const error = ref('');
const upload = ref(null);
const lastFile = ref(null);
const dragOver = ref(false);
const strategy = ref('grid');

const MAX_BYTES = 5 * 1024 * 1024;

const canApply = computed(() => phase.value === 'parsed' && (upload.value?.drafts?.length ?? 0) > 0);
const busy = computed(() => phase.value === 'parsing' || phase.value === 'optimizing');

const summary = computed(() => upload.value?.summary ?? null);
const drafts = computed(() => upload.value?.drafts ?? []);
const room = computed(() => upload.value?.room ?? null);

function openPicker() {
  if (busy.value) return;
  fileInput.value?.click();
}

async function handleFiles(files) {
  const file = files && files[0];
  if (!file) return;
  error.value = '';
  upload.value = null;
  lastFile.value = file;

  if (file.size > MAX_BYTES) {
    phase.value = 'error';
    error.value = `That file is ${(file.size / 1024 / 1024).toFixed(1)} MB — the limit is 5 MB.`;
    return;
  }
  const name = (file.name || '').toLowerCase();
  if (!/\.(xlsx|xlsm|csv|txt)$/.test(name)) {
    phase.value = 'error';
    error.value = 'Unsupported file type. Upload an .xlsx or .csv equipment list.';
    return;
  }

  phase.value = 'parsing';
  try {
    upload.value = await parseEquipmentUpload(file);
    phase.value = 'parsed';
  } catch (err) {
    phase.value = 'error';
    error.value = err?.message || String(err);
  }
}

function onFileChange(event) {
  handleFiles(event.target?.files);
  if (event.target) event.target.value = ''; // allow re-selecting the same file
}

function onDrop(event) {
  dragOver.value = false;
  if (event.dataTransfer?.files?.length) handleFiles(event.dataTransfer.files);
}

function reset() {
  phase.value = 'idle';
  upload.value = null;
  lastFile.value = null;
  error.value = '';
}

function close() {
  reset();
  emit('close');
}

function applyToScenario() {
  if (!canApply.value) return;
  emit('apply', { upload: upload.value, strategy: strategy.value });
}

async function optimize() {
  if (!lastFile.value || busy.value) return;
  error.value = '';
  phase.value = 'optimizing';
  try {
    const result = await simulateUpload(lastFile.value, { strategy: strategy.value });
    emit('optimize', result);
    phase.value = 'parsed';
  } catch (err) {
    phase.value = 'error';
    error.value = err?.message || String(err);
  }
}
</script>

<template>
  <section
    class="mx-auto w-full max-w-7xl px-6 pb-2"
    role="region"
    aria-label="Upload equipment list"
  >
    <div
      class="relative rounded-xl border border-teal-700/40 bg-slate-950/80 p-4 shadow-lg shadow-black/30"
    >
      <button
        type="button"
        class="absolute right-3 top-3 rounded p-1 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
        aria-label="Close upload panel"
        @click="close"
      >
        ✕
      </button>

      <!-- idle / parsing / error: the drop zone -->
      <template v-if="phase !== 'parsed'">
        <div
          class="flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-6 text-center transition"
          :class="
            dragOver
              ? 'border-teal-400 bg-teal-500/10'
              : 'border-slate-700 bg-slate-900/40'
          "
          @dragover.prevent="dragOver = true"
          @dragleave.prevent="dragOver = false"
          @drop.prevent="onDrop"
        >
          <p class="text-sm font-medium text-slate-200">
            <span v-if="phase === 'parsing'">Parsing…</span>
            <span v-else-if="phase === 'optimizing'">Running the agent on your hardware…</span>
            <span v-else>📥 Drop your fab equipment list here</span>
          </p>
          <p class="text-[11px] text-slate-500">
            .xlsx or .csv · columns for name, watts, dB, and (optionally) footprint in metres
          </p>
          <button
            type="button"
            class="mt-1 rounded-lg border border-teal-600/60 bg-teal-500/10 px-3 py-1.5 text-xs font-semibold text-teal-200 transition hover:bg-teal-500/20 disabled:opacity-50"
            :disabled="busy"
            @click="openPicker"
          >
            {{ busy ? 'Working…' : 'Choose a file' }}
          </button>
          <input
            ref="fileInput"
            type="file"
            accept=".xlsx,.xlsm,.csv,.txt"
            class="hidden"
            @change="onFileChange"
          />
        </div>

        <p
          v-if="error"
          role="alert"
          class="mt-2 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
        >
          {{ error }}
        </p>
      </template>

      <!-- parsed: summary table + actions -->
      <template v-else>
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <p class="text-sm font-medium text-emerald-300">
            ✅ {{ drafts.length }} machine{{ drafts.length === 1 ? '' : 's' }} parsed
            <span class="text-slate-400">from {{ upload?.source_filename }}</span>
          </p>
          <span v-if="room?.width_m" class="font-mono text-[11px] text-slate-500">
            room {{ room.width_m }}×{{ room.depth_m }} m
          </span>
          <span v-if="summary?.rows_skipped" class="font-mono text-[11px] text-amber-400">
            {{ summary.rows_skipped }} skipped
          </span>
        </div>

        <div class="max-h-40 overflow-auto rounded border border-slate-800">
          <table class="w-full border-collapse text-left font-mono text-[11px]">
            <thead class="sticky top-0 bg-slate-900 text-slate-400">
              <tr>
                <th class="px-2 py-1">machine</th>
                <th class="px-2 py-1">type</th>
                <th class="px-2 py-1">w×h</th>
                <th class="px-2 py-1">watts</th>
                <th class="px-2 py-1">dB</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="d in drafts"
                :key="d.id"
                class="border-t border-slate-800/70 text-slate-300"
              >
                <td class="max-w-[14rem] truncate px-2 py-1">{{ d.name }}</td>
                <td class="px-2 py-1">
                  <span v-if="d.machine_type" class="text-teal-300">{{ d.machine_type }}</span>
                  <span v-else class="text-amber-400">unknown</span>
                </td>
                <td class="px-2 py-1">{{ d.w }}×{{ d.h }}</td>
                <td class="px-2 py-1">{{ d.heat_watts }}</td>
                <td class="px-2 py-1">{{ d.vibration_db }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <p v-if="summary?.types_unrecognized?.length" class="mt-2 text-[11px] text-amber-400">
          Unrecognised types (edit these in the editor): {{ summary.types_unrecognized.join(', ') }}
        </p>

        <p v-if="error" role="alert" class="mt-2 text-[11px] text-rose-300">{{ error }}</p>

        <div class="mt-3 flex flex-wrap items-center gap-2">
          <label class="flex items-center gap-2 text-[11px] text-slate-400">
            placement
            <select
              v-model="strategy"
              class="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-slate-200 focus:border-teal-500 focus:outline-none"
            >
              <option value="grid">grid</option>
              <option value="load_based">heat-aware</option>
              <option value="random">random</option>
            </select>
          </label>

          <button
            type="button"
            class="rounded-lg border border-teal-500 bg-teal-500/15 px-3 py-1.5 text-xs font-semibold text-teal-200 transition hover:bg-teal-500/25 disabled:opacity-50"
            :disabled="!canApply || busy"
            @click="applyToScenario"
          >
            Apply to editor →
          </button>
          <button
            type="button"
            class="rounded-lg border border-fuchsia-500/60 bg-fuchsia-500/10 px-3 py-1.5 text-xs font-semibold text-fuchsia-200 transition hover:bg-fuchsia-500/20 disabled:opacity-50"
            :disabled="busy"
            @click="optimize"
          >
            {{ phase === 'optimizing' ? 'Optimizing…' : '⚡ Optimize now' }}
          </button>
          <button
            type="button"
            class="rounded px-2 py-1 text-[11px] text-slate-500 transition hover:text-slate-300"
            @click="reset"
          >
            upload different file
          </button>
        </div>
      </template>
    </div>
  </section>
</template>
