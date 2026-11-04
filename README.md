# fabArchitect AI

> **A stateless CAD simulator powered by Meta's Model API, with a Fireworks AI
> fallback.**
> Stateless · serverless · production-grade prototype for the Meta AI hackathon.

**fabArchitect AI** is a real-time decision tool for placing semiconductor
machines on a 100×100 cleanroom grid under thermal and vibration constraints.
The whole pipeline runs **in-memory**, converges in **20 – 80 seconds**, and
ships **without a database**. The Meta Model API drives the corrector in
production; Fireworks AI is the dev / fallback story. They cooperate through a
verifier-gated loop.

📺 **Watch it in action:** [YouTube — @ibtcmalu6101](https://www.youtube.com/@ibtcmalu6101)

---

## The problem

In a semiconductor cleanroom, three forces fight for the same floor:

1. **Heat.** Litho scanners (380 W), ion implanters (340 W), diffusion furnaces
   (400 W). The FDM heat field peaks where their Gaussian fields overlap.
2. **Vibration.** Two machines within a critical distance **and** ≥ 70 dB
   contaminate each other. Litho scanners at 92 dB cannot coexist with
   polishers at 96 dB.
3. **Wall clearance.** The solver uses **zero-flux (reflective) boundaries** —
   heat concentrates at walls and corners. Hot racks must sit **≥ 8 cells**
   from any wall.

The interesting question is **whether these machines can share the same floor
without breaking the thermal limit (≤ 26 °C) or violating exclusion zones —
and what the trade-offs are.** That is a continuous constraint-satisfaction
problem with no clean optimum: every practical layout is a compromise. A
process engineer needs, *before* committing capital, a verdict **and** a
documented reason.

---

## How fabArchitect AI solves it

Four cooperating layers run in sequence for every `/simulate` request:

| Layer | Component | What it does | When it fires |
|---|---|---|---|
| **FDM heat solver** | `core/heat_solver.py` | 200 timesteps on a 100×100 grid with zero-flux boundaries. Reports peak temperature. | Always |
| **Vibration analyzer** | `core/vibration.py` | Pairwise distance + dB check; flags close pairs ≥ 70 dB. | Always |
| **LLM** | `core/providers.py` (Meta first, Fireworks fallback) | Reads the simulated scene + error codes and proposes JSON layout adjustments. | When at least one error fires |
| **Trust-but-verify gate** | `agents/corrector.py` | **Re-runs the FDM on the proposed plan** before accepting it. Rejected plans fall through to `local_relax(greedy=True)`. | Every model proposal |

### The model chain — Meta primary, Fireworks dev / fallback

We pick a model by intent, not by accident:

| Role | Provider | Model id | When |
|---|---|---|---|
| **Primary — text reasoning** | **Meta Model API** (`https://api.llama.com/v1`) | `meta-muse-wonder-2026` | Production deployment on Modal (`META_LLAMA_KEY` set) |
| **Primary — spatial renderer (optional)** | **Meta Model API** | `meta-muse-image-2026` | Production; only if an image model is configured |
| **Fallback — text reasoning + spatial** | Fireworks AI (`https://api.fireworks.ai/inference/v1`) | `accounts/fireworks/models/muse-spark-open` | Local dev (`META_LLAMA_KEY` empty) and any deployment where the Meta primary fails |

Configured via env (`PROVIDER_PRIMARY=meta` by default; the router falls
through automatically on timeout / 4xx / empty content). Every response returns
the names of the providers that actually ran under `providers_used`, so you can
tell at a glance whether the answer came from the Meta Model API directly or
fell through to the Fireworks fallback.

Both stages have a cross-provider Fireworks mirror: `muse-spark-open` (the
Meta-family reasoning model that *is* available on this Fireworks account)
serves the strategy stage via `FIREWORKS_MODEL_ID`, and the spatial stage via
`FIREWORKS_IMAGE_MODEL_ID`. If both Meta and Fireworks are unavailable, the
spatial renderer falls back to a deterministic local rasteriser, and the
strategy stage falls back to deterministic physics-only relaxation.

### Provider stats

| | Meta Model API (`meta-muse-wonder-2026`) | Fireworks fallback (`muse-spark-open`) |
|---|---|---|
| Reasoning tokens / call | reasoning model — ~1k+ | reasoning model — **~640** at `reasoning_effort=low` |
| End-to-end `/simulate` | 40 – 80 s | **5 – 15 s** (≈4 s per corrector turn) |
| JSON mode (`response_format`) | native | native |
| Role | production primary on Modal | local dev, CI, last-resort fallback |
| Headroom needed | higher token budget | `FIREWORKS_MAX_TOKENS>=4096` + `FIREWORKS_REASONING_EFFORT=low` |

### Why trust-but-verify

If you asked an LLM directly to optimize this layout, the natural priors it
would use are wrong. At seed 42 the model proposed *"move every hot rack into
the interior, walls are bad"*. But interior is exactly where Gaussian heat
fields overlap and **add**; the peak went from 31.49 °C to 37.37 °C. The FDM in
the gate caught it; deterministic relaxation delivered an answer instead.
Without the gate, "AI solve" would be a worse heuristic than a script.

### Stack at a glance

| Domain | Stack | Deploy target |
|---|---|---|
| Backend | Django + Django Ninja (in-memory settings, no `manage.py`), NumPy + SciPy FDM heat solver, dual-provider LLM router (Meta Model API → Fireworks AI) | Modal serverless sandbox |
| Frontend | Vue 3 + Vite 5 + TailwindCSS 3.4 + HTML5 Canvas | Vercel |

## How a user interacts with it

### Mode 1 — Autonomous (default)

Press **▸ Run simulation** with a seed. The pipeline:

```
seed 42  ─→  generate_scene(42)
            │   8 deterministic machines, non-overlapping
            ▼
          simulate_scene()                     # peak 31.49 °C, 6 hazards
            │
            ▼
          ModelRouter.complete()
            │   primary  : Meta Model API  (meta-muse-wonder-2026)
            │   fallback : Fireworks AI    (muse-spark-open, low reasoning)
            ▼
          Trust-but-verify                    # re-simulate, accept or reject
            │   accept  → phase:"correct"
            │   reject  → phase:"verify" + local_relax(greedy=True)
            ▼
          repeat ≤ 3 turns
            │
            ▼
          SimulateResponse: { before, after, delta_t, corrections[],
                              providers_used, spatial }
```

### Mode 2 — Co-pilot (scenario editor)

Click **✎ Edit layout**, then drag any of the 8 catalog machines from the
right-hand palette onto the canvas. The live constraint badge updates inside
250 ms **without** calling the LLM. Click **🤖 Compute suggestions** and the
same corrector runs on **your** layout — Meta first, Fireworks falls back if
needed.

```
autonomous       co-pilot
+--------+        +--------+
| run()  |        | drop   |
+--------+        +--------+
     |                 |
     v                 v
evaluate(cfg)    evaluate(cfg)
     |                 |
     v                 v
LLM proposes      LLM proposes  (uses YOUR scene, not a generated one)
     |                 |
     v                 v
TRUST-BUT-VERIFY (same gate, same fallback, same physics)
     |                 |
     v                 v
correct or verify  correct or verify
```

---

## How to read the outputs

Four signals, always present:

| Signal | What to look at | Why |
|---|---|---|
| `before` / `after` | Simulated peak at baseline and at the end | The headline numbers |
| `delta_t = before.peak_c − after.peak_c` | Improvement (always ≥ 0 — the gate guarantees no regression) | Verdict at a glance |
| `corrections[]` | Turn-by-turn log of what the corrector did | The reasoning trail |
| `providers_used` | Names of the providers that actually ran | Production: `['meta']`. Dev: `['fireworks']`. A mixed list means one turn fell through. |

Each turn carries `phase` (the kind of step), `error_codes` (what the verdict
fired), `summary` (the model's plain-English justification, capped at 400
chars), and `peak_c` (the simulation after that turn).

### The four phases

| Phase | Meaning |
|---|---|
| `evaluate` | The solver ran and reported its verdict — **every** turn has one. |
| `correct` | The proposed plan was **accepted** after re-simulation. |
| `verify` | The proposal was caught raising the peak; deterministic fallback engaged. |
| `error` | A hard provider failure; the corrector returned what it had. |

### The error codes you'll see

| Code | Meaning |
|---|---|
| `E_TEMP_PEAK` | Peak > 26 °C operating limit — the most common violation. |
| `E_VIBRATION_CONFLICT` | Two high-dB machines in critical proximity. |
| `E_REJECTED_PLAN` | The model's plan was rejected by the gate — the best part of the trail, because it shows the verifier held the line. |
| `E_PROVIDER` | Both providers failed; deterministic relaxation completed the run. |
| `E_EMPTY_CONTENT` | The provider burned its token budget without emitting output. |
| `E_EMPTY_PLAN` | The provider returned zero usable adjustments. |

### Spatial output

`spatial.spatial_spec.footprints` is the canvas-ready geometry (boxes, labels,
color token, per-box `hazards`). `spatial.preview_png_data_url` is a
server-rendered PNG of the floor — pixels are produced by an in-house string-art
decoder (`core/png.py`, `core/font5x7.py`), **not** a generative model. Each box
gets an orange stripe on its left edge when it is a thermal hotspot and a violet
stripe on its right edge when it participates in a vibration pair (see
[Hazard indicators](#hazard-indicators-thermal-vs-vibration) below). When the
optional Meta image endpoint is configured the thumbnail shows
`source: MUSE_IMAGE`, otherwise `source: FALLBACK`.

---

## Reading the dashboard, panel by panel

`How a user interacts with it` and `How to read the outputs` describe the
`/simulate` payload a caller receives. This section maps the *rendered*
dashboard a reviewer sees — which panel translates which field, and where
each number comes from. The UI is a three-column Vue view
(`frontend/src/App.vue`); each column corresponds to one leg of the
pipeline — physics verdict, floor plan, reasoning trace.

### Left column — physics verdict

| Panel | What it shows | What to look at |
|---|---|---|
| **Thermal telemetry** | `before.peak_c`, `after.peak_c`, `ΔT`, Improvement %, `providers_used`, and wall-clock elapsed | **`After peak ≤ 26 °C`** is the only hard pass/fail number. Everything else is commentary. |
| **Scene summary** | One row per machine with its `heat_watts`, `vibration_db`, and **From → To** grid coordinates | Green-highlighted rows are machines the corrector actually moved. The green-row count is a fair proxy for how much real work the agent did. |
| **Spatial preview** | The 500×500 PNG tile, its provenance badge, and the `overlaps` / `clipping` flags | `Local` (amber) means the dependency-free fallback rasteriser drew it. `Muse Image` (teal) means the Meta image model ratified the layout. On a Fireworks-only deployment it is always `Local` — that is expected, not broken. |

### Centre column — the floor

A 500×500 pixel `<canvas>` at 5 px per cell (`FloorplanCanvas.vue`). Layers,
painted back-to-front:

1. **Black background + grid lines** — the 100×100 logical floor.
2. **Orange thermal overlay** — cells at or below 22 °C stay dark; cells
   above are tinted up to a peak around 30 °C.
3. **Red vibration halos** — drawn around each pair of ≥ 70 dB machines
   that sit inside the critical distance.
4. **Teal dashed machine footprints** — the *corrected* layout, never the
   raw synthetic input scene.

The bottom-left label shows live `peak XX.XX C` and confirms grid/cell
sizing.

### Right column — reasoning and editor

| Panel | What it shows |
|---|---|
| **Equipment catalog** | The 8 machine types the synthetic generator can place. Every card carries the same `w`, `h`, `heat_watts`, `vibration_db` the catalog uses — so a card you drop and a machine the generator places are byte-identical. Drag onto the canvas, or tap a card then tap the floor. |
| **Agent event log** | The `corrections[]` array rendered as rows. Left border color encodes the `phase` — sky `evaluate`, emerald `correct`, violet `verify`, amber `error`. The `peak_c` chip beside each row lets you follow the corrector's trajectory (falling / flat / bouncing). |

### Editor-mode overlay — the constraint badge

When at least one machine is dropped into the canvas, a **ConstraintBadge**
appears at the top-right. It is fed by `POST /api/v1/simulate/synopsis`
— **physics-only, no LLM** — debounced to 250 ms after each drag settle
(`useScenarioStore.js`). The color encodes the worst finding, not a score:

| Tone | Meaning |
|---|---|
| emerald | clean — `error_codes` empty |
| amber | hazard present, but peak ≤ 26 °C |
| rose | preview unavailable (rate-limit `429` or network) |
| sky | solving — request in flight |

Hitting **Compute suggestions** runs the full corrector on the layout you
built and hydrates the returned `final_layout` straight back into the
editor (`App.vue:105`), so you can keep nudging from the agent's best
answer.

### Iterating toward a target — the seed loop

The app is stateless, so iteration is **by seed**, not by editing the
previous result in place:

1. **Run simulation** with an empty seed input — the backend picks a fresh
   random seed each click, and `core/synthetic_gen` deterministically
   builds a fresh cleanroom from it.
2. Watch `After peak` and `Improvement %`. A higher improvement means the
   corrector genuinely did work; near-zero means the scene was already
   compliant and the agent was idle.
3. Type a specific integer into the seed input and re-run to lock the
   starting scene. Rerunning a seed re-generates the same `before` scene
   every time, with only the agentic corrections varying slightly.
4. Switch to **editor mode** to eliminate variance entirely — build the
   layout you want, then hit **Compute suggestions** once.

### When is a run "done"?

The corrector stops short of all 3 turns **iff** all three conditions hold
(`corrector.py:481-485` + `router.py:144`):

```
After.peak_c  ≤ 26.0 °C             # TEMP_LIMIT_C, agents/schemas.py
After.hazards = 0                    # no E_TEMP_PEAK, no E_VIBRATION_CONFLICT
working layout == lastAccepted layout (no measurable progress)
```

Two clean-shaped terminations:

- **Clean short-circuit** — Event Log has zero or one entry and
  `providers_used` may be empty. The synthetic scene was already within
  limits, so **no LLM was ever called**. That is correct, not a bug.
- **Converged after correction** — `After peak` is stable across reruns
  with the same seed, and the last `verify` row's `peak_c` equals
  `After.peak_c`.

Three *false* "done" traps to avoid:

| Panel read | What is actually happening |
|---|---|
| `Improvement % = 0`, `hazards = 0` | The scene arrived already compliant; the corrector never fired. |
| `After peak = 26.00` exactly | Right on the limit — one floating-point unit away from tripping `E_TEMP_PEAK`. Not a converged result. |
| Three `verify` rows with `E_REJECTED_PLAN` | The model repeatedly proposed worse layouts; `local_relax(greedy=True)` carried the run. The result is still the best layout seen — never a regression. |

The single hard gate judges should care about is `After.peak_c > 26 °C`.
Above that, nothing else in the panels matters. Below it, you can layer
taste-based criteria on top — peak ≤ 24 °C for thermal headroom, zero
`E_VIBRATION_CONFLICT` codes for vibration isolation, fixed-seed replays
for reproducible screenshots.

---

## A worked example

### Mode 1 — Autonomous run, seed = 42

The same scene, run twice: once against the production Meta key (deployed on
Modal) and once against the dev Fireworks key. The corrector behaves
identically; `providers_used` tells you which path answered.

**Production — Meta Model API on Modal:**

```
before                       peak 31.49 °C,    6 hazards
T1 evaluate [E_TEMP_PEAK]    peak 31.49 °C,    6 hazards
T1 verify    [E_REJECTED_PLAN] proposer wanted hot clusters in the interior;
                              the FDM says that overlaps the Gaussians - reject
T2 evaluate [E_TEMP_PEAK]    peak 31.34 °C,    5 hazards
T2 verify    [E_REJECTED_PLAN] deterministic fallback kept the improvement
T3 evaluate [E_TEMP_PEAK]    peak 31.34 °C,    5 hazards
after                        peak 31.34 °C,    5 hazards
─────────────────────────
delta_t         +0.15 °C      (the gate guarantees no regression)
providers_used   ['meta']     <- Meta Model API answered every turn
elapsed          40 - 80 s on Modal
```

**Dev — Fireworks fallback on a laptop:**

```
before                       peak 31.49 °C,    6 hazards
... same trail ...
after                        peak 31.34 °C,    5 hazards
─────────────────────────
delta_t         +0.15 °C
providers_used   ['fireworks']   <- same answer, faster, Meta key was empty
elapsed          4 - 13 s
```

### Mode 2 — Co-pilot walkthrough

```
you drop a 380 W litho scanner near the bottom-right wall
                                        -> badge: peak 32.7 C, 6 hazards  (red)
you drag it 12 cells into the interior
                                        -> badge: peak 28.1 C, 2 hazards  (amber)
you click Compute suggestions
                                        -> Meta Model API proposes, gate verifies
                                        -> final: peak 26.4 C, 0 hazards  (green)
you drag the worst machine back 4 cells
                                        -> you click Compute suggestions again
                                        -> the agent re-corrects from your refinement
```

### Headline numbers across seeds

| Seed | Before | After | Δ peak | Hazards |
|---|---|---|---|---|
| 42 | 31.49 °C | 31.34 °C | −0.15 | 6 → 5 |
| 7 | 32.09 °C | 31.55 °C | −0.55 | 6 → 5 |
| 99 | 37.17 °C | 31.38 °C | −5.79 | 7 → 6 |

Zero regressions across every measured seed; the gate enforces that. Two of
those seeds had **every** model proposal rejected by the verifier — the
deterministic relaxation still delivered the improvements above, which is the
actual point of the dual-mode design: **the model gets a voice, the solver
gets a veto.**

---

## Architecture, in one diagram

```
+----------------------+                       +----------------------+
|  META MODEL API      | proposes a layout     |  META MODEL API      |
|  meta-muse-wonder    | ------------------+   |  meta-muse-image     |
|  primary text model  |                   |   |  optional canvas     |
+----------+-----------+                   |   +----------+-----------+
           |                               |              |
           v                               |              v
+--------------------------------------+   |  +----------------------+
|           c o r r e c t o r          |   |  |  spatial renderer    |
|  +--------+ +--------+ +-----------+ |   |  |  sanitize_layout,    |
|  |  FDM   | |  vib   | | trust-but-| |   |  |  RLE PNG, 5x7 font,  |
|  | solver | | check  | | verify    | |   |  |  footprints          |
|  +--------+ +--------+ +-----------+ |   |  +----------+-----------+
+------------------+-------------------+   |             |
                   |                       |             |
                   v                       |             v
        +----------------------------------------------------+
        |      Vue 3 + Vite 5 dashboard (HTML5 canvas)        |
        +----------------------------------------------------+
                   ^
                   |  fallback / dev path
+--------------------------+
|  FIREWORKS AI            |  used when the Meta key is empty
|  muse-spark-open         |  or the primary call fails
|  (low reasoning, 4096)   |
+--------------------------+

Always available: deterministic gradient relaxation (local_relax) -
the verifier veto that kept seed 42 and seed 99 from regressing
even when every model proposal was wrong.
```

## Prerequisites

- **Python ≥ 3.11** (verified on 3.13.3)
- **Node ≥ 26** (verified on 26.13.0) + npm ≥ 11
- No databases, migrations, or persistent storage are used anywhere.

## Repository layout

```
backend/    Django Ninja API, physics simulation, multi-agent correction loop
frontend/   Vue 3 + Vite SPA, canvas floorplan dashboard
scripts/    Dev + e2e smoke helpers
```

## Local development

### Backend (port 8080)

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt pytest pytest-asyncio
python run_local.py
# → http://127.0.0.1:8080/api/v1/health
```

### Frontend (port 5173)

```powershell
cd frontend
npm install
npm run dev
# → http://localhost:5173 (proxies /api to :8080)
```

### One-shot E2E smoke

```powershell
powershell -ExecutionPolicy Bypass -File scripts/e2e_smoke.ps1
```

## Environment

Copy `backend/.env.example` → `backend/.env` for local testing. In production
(Modal) all secrets are injected via `modal.Secret.from_name("llm-keys")`; the
`.env` file is intentionally excluded from Modal image builds.

## API surface

| Method | Path | Description | Rate limit (per IP) |
|---|---|---|---|
| `GET` | `/api/v1/health` | Liveness + build metadata | `120/min` |
| `GET` | `/api/v1/health/deep` | Dependency versions + provider key presence | `30/min` |
| `POST` | `/api/v1/simulate` | Full physics + agentic self-correction pipeline | `12/min` |
| `POST` | `/api/v1/simulate/synopsis` | Physics-only verdict (no LLM) for the live editor | `60/min` |
| `GET` | `/api/v1/equipment/catalog` | Draggable machine types (scenario editor) | `120/min` |
| `POST` | `/api/v1/equipment/parse` | Parse an uploaded XLSX/CSV into equipment drafts | `10/min` |
| `POST` | `/api/v1/equipment/place` | Auto-place parsed drafts onto the grid | `10/min` |
| `POST` | `/api/v1/simulate/upload` | Parse + place + full simulation from one uploaded file | `12/min` |

Rate limits are configurable via `RATE_SIMULATE`, `RATE_HEALTH`,
`RATE_HEALTH_DEEP`, `RATE_ANON` and `TRUSTED_PROXY_HOPS` (see
`backend/.env.example`). Exceeding a limit returns `429` with a structured
body and a `Retry-After` header:

```json
{
  "detail": "Rate limit exceeded. Slow down and retry shortly.",
  "code": "E_RATE_LIMITED",
  "retry_after_seconds": 42
}
```

Each endpoint has its own counter bucket, so hammering `/simulate` never eats
into the `/health` budget. Counters live in Django's `LocMemCache`, i.e. they
are **per container** — with N warm Modal containers the aggregate ceiling is
about N × the configured rate. That keeps the "no external state" invariant
intact.

`POST /api/v1/simulate` accepts:

```json
{ "seed": 42, "equipment": [/* optional overrides */] }
```

and returns:

```json
{
  "seed": 42,
  "providers_used": ["meta"],
  "scene": { "seed": 42, "grid_size": 100, "equipment": [...] },
  "before": { "peak_c": 31.49, "temp_grid_rle": "200:... ", "hazards": [...] },
  "after":  { "peak_c": 24.81, "temp_grid_rle": "200:... ", "hazards": [] },
  "delta_t": 6.68,
  "corrections": [ /* <=3 turn reasoning trail */ ],
  "final_layout": [ /* optimized machine positions */ ],
  "spatial": {
    "spatial_spec": {
      "footprints": [
        { "id": "LITHO_SCANNER-00", "origin": [12, 30], "size": [8, 6],
          "label": "Litho Scanner", "z_index": 0, "color_token": "teal" }
      ],
      "canvas": {
        "grid_size": 100, "cell_size_px": 5, "canvas_px": [500, 500],
        "no_overlap": true, "no_clipping": true,
        "source": "MUSE_IMAGE", "notes": []
      }
    },
    "preview_png_data_url": "data:image/png;base64,iVBORw0KGgo..."
  },
  "spatial_source": "MUSE_IMAGE"
}
```

## The two-stage pipeline

The **primary** stage-1 and stage-2 providers are both on the **Meta Model API**
(`https://api.llama.com/v1`); only the model id differs. `Muse Wonder` /
`Muse Image` are model-id codenames, not separate providers. Each stage has a
**Fireworks mirror** so a Meta outage degrades one provider, not the whole
pipeline.

| Stage | Meta primary (env) | Fireworks mirror (env) | Role |
|---|---|---|---|
| 1 | **Muse Wonder** — `META_MODEL_ID` (default `meta-muse-wonder-2026`) | `FIREWORKS_MODEL_ID` (default `accounts/fireworks/models/muse-spark-open`) | Strategy engine: ingests the scene + sim errors, proposes corrected coordinates, re-simulated for up to 3 turns |
| 2 | **Muse Image** — `META_IMAGE_MODEL_ID` (default `meta-muse-image-2026`) | `FIREWORKS_IMAGE_MODEL_ID` (default `accounts/fireworks/models/muse-spark-open`) | Spatial renderer: takes the winning layout matrix, emits canvas-ready `footprints` + a PNG preview |

If both stage-2 providers are unreachable, stage 2 still runs a **deterministic
local renderer** (clamp → push-out overlaps → pure-Python PNG) and reports
`source: "FALLBACK"`. The canvas spec is always valid; the UI shows which source
produced it. Stage 1 falls back to deterministic physics-only relaxation the
same way.

The local renderer draws machine names using a bundled **5×7 bitmap font**
(`core/font5x7.py`) — no Pillow, no fonts to install. Placement ladder:

1. full label inside the footprint at 2× scale;
2. full label inside at 1× scale;
3. full label annotated beside the box (right, else left);
4. truncated annotation on whichever side has more room.

Footprint fill/edge/label colors follow `color_token`
(`teal` / `warn` / `danger`), so a hazard flagged by Muse Image visibly changes
the box color in the preview.

### Hazard indicators: thermal vs vibration

`spatial.spatial_spec.footprints[*].hazards` is a per-machine list
(`"thermal"` / `"vibration"`) derived by the Python solver — **never** invented
by the model. Two channels use it so the two hazard classes are visually
distinct everywhere:

| Hazard | Colour | Where it appears |
|---|---|---|
| **Thermal** (peak > 26 °C) | **Orange** `#f97316` | Orange heat-grid cells **+ a 2px stripe on the box's *left* edge** |
| **Vibration** (two ≥70 dB machines in critical proximity) | **Violet** `#8b5cf6` | Violet halo ring around each participant **+ a 2px stripe on the box's *right* edge** |

A machine that is *both* a thermal hotspot and a vibration participant carries
**both stripes** (orange left, violet right) — the two indicators stack rather
than compete. The mapping is byte-identical in the backend PNG
(`core/png.py::_draw_hazard_stripes`) and the frontend canvas
(`FloorplanCanvas.vue::drawHazardStripes`), so the thumbnail and the live canvas
never disagree.

Derivation rules (`spatial_renderer.py::_derive_hazards`):

- **Source of truth** — the solver (`core/heat_solver.py`, `core/vibration.py`)
  decides *which* machines are in trouble; the renderer never re-guesses
  geometry.
- **Vibration** — *both* sides of every `type=="vibration"` hazard get the token
  (the pair is marked symmetrically).
- **Thermal** — every machine that produced a `type=="thermal"` hazard (its
  footprint centre exceeded 26 °C). The solver emits one per violating machine,
  so all of them are striped, not just the single hottest one. A safety net
  covers the edge case where the global peak exceeds the limit *between* two
  machines: if no per-machine thermal hazard was emitted, the machine nearest
  `peak_at` is marked instead.
- **Union across the run** — the corrector passes `[baseline_sim, best_sim]`, so
  the tags are unioned per machine id. This is deliberate: the corrector usually
  *resolves* vibration, so drawing from `best_sim` alone would hide exactly the
  conflicts the agent detected and fixed. The stripes tell the explainability
  story — "this machine had a thermal / vibration problem during the run" — even
  once it has been moved clear.

The `ConstraintBadge` follows the same palette for the summary code:
`E_TEMP_PEAK` → rose, `E_VIBRATION_CONFLICT` → violet, both → fuchsia.

> **Why a demo run may show no violet stripes:** vibration is rare in the
> synthetic generator (24 of 300 seeds) and, when present, the corrector usually
> resolves it. Use a seed such as **63, 74, 78, or 84** to see the vibration
> stripes; seed 42 has no vibration conflict at all. In the scenario editor, drop
> two high-dB machines (e.g. two CMP Polishers, 96 dB) close together. The
> frontend hazard overlay unions `before` and `after`, so a *resolved* conflict
> still paints its violet halo.

The frontend paints `spatial.spatial_spec.footprints` (teal dashed boxes) over
the thermal/vibration overlays, and renders `preview_png_data_url` as a
thumbnail tile beside the live canvas.

### Fireworks model selection (read this before changing the model id)

**Verified live against `/inference/v1/models` for this project's key (Nov 2026):**
30 visible ids, **zero** matching `llama` — the account does *not* serve open
Llama weights. It *does* serve `muse-spark-open`, the Meta-family reasoning
model, which is what the fallback uses. Four ids that look plausible but return
`404 Model not found, inaccessible, and/or not deployed` on both the local dev
key and the production `llm-keys` Modal secret: `muse-glimmer-30b`,
`llama-v3p1-70b-instruct-1b`, `llama4-maverick-instruct-basic`,
`llama4-scout-instruct-basic`. Always confirm before pinning.

Two scripts exist so this is never guesswork:

```bash
python scripts/list_fireworks_models.py            # what can this key reach?
python scripts/probe_raw.py --model <model-id>     # live correctness probe
```

The default is `accounts/fireworks/models/muse-spark-open`, run with
`FIREWORKS_REASONING_EFFORT=low` and `FIREWORKS_MAX_TOKENS=4096`. At that
setting it emits schema-valid `{"adjustments": [...]}` **and**
`{"footprints": [...]}`, so one model covers both stages. Measured behaviour of
the reachable candidates:

| Model | Latency | Reasoning tokens | Valid JSON |
|---|---|---|---|
| **`muse-spark-open`** @ `reasoning_effort=low`, 4096 cap | ~4 s | **640** | **yes** — both schemas |
| `minimax-m3` *(retired default)* | 1.2 s | 0 | yes — but being de-listed |
| `minimax-m3p1-flash` @ 1024 **and** @ 4096 cap | — | 1024 / 4096 | **no** — `finish_reason=length`, content empty |
| `nemotron-3-ultra-nvfp4` | 1.7 s | 0 | ignored JSON mode, 2.8 kB of prose |
| `inkling` | 74 s | 0 | yes, but far too slow |

> If you point `FIREWORKS_MODEL_ID` at a chat-instruct model, set
> `FIREWORKS_MAX_TOKENS=1024` **and** `FIREWORKS_REASONING_EFFORT=` (explicitly
> empty) — chat-instruct models reject the `reasoning_effort` field outright.

### Trust-but-verify: the model proposes, the solver decides

The single most important rule in `agents/corrector.py`. The LLM cannot run the
FDM solver, so a fluent, confident plan can still be **worse** than what it
started with. Observed live on seed 42: the model moved every hot rack "into
the interior", which is exactly where the Gaussian heat fields overlap and add,
taking the peak from **31.49 → 37.37 °C**.

Every plan is therefore re-simulated against the same composite score the loop
uses, and kept only if it actually wins:

```
score = max(0, peak - 26) + hazards * 2 + max(0, peak - baseline_peak) * 3
```

Rejected plans are recorded as `phase="verify"` with `E_REJECTED_PLAN` and the
deterministic `local_relax(greedy=True)` takes over. That fallback enumerates a
small move set and verifies each candidate against the solver, so it cannot
regress either. Net effect across seeds: no seed ever ships a hotter layout.

| seed | before | after | Δ peak | hazards |
|---|---|---|---|---|
| 42 | 31.49 °C | 31.34 °C | −0.15 | 6 → 5 |
| 7 | 32.09 °C | 31.55 °C | −0.55 | 6 → 5 |
| 99 | 37.17 °C | 31.38 °C | −5.79 | 7 → 6 |

## Scenario editor — the user drives, the agent still corrects

The dashboard has two producers for the same `Scene`. The agent cannot tell
them apart, which is the whole design:

```
generate_scene(seed) ─┐
                      ├─→ Scene ─→ run_correction() ─→ final_equipment
catalog + drag/drop ──┘
```

## Bring your own equipment list (XLSX / CSV)

A judge (or any operator) usually arrives with their own machine list, not a
seed. The **📥 Upload equipment** button in the header opens a full-width banner:
drop an `.xlsx` or `.csv`, review the parsed table, pick a placement strategy,
and either **Apply to editor** (position the machines, then let the agent run)
or **⚡ Optimize now** (one shot: parse → place → full agentic correction).

### File format

The parser is column-tolerant. At minimum it needs a **name** (or id), a
**watts** column and a **dB** column. Footprint and room dimensions are optional.

```
Cleanroom: Bay 7, 40, 25            ← optional room block (metres): width, depth

name,type,power (W),noise (dB),width (m),height (m)
ASML Stepper 1980,stepper,380,92,4,3
LAM Etcher,etch,220,78,2.5,2.5
CMP Polisher 3,cmp,1800,96,3,2
```

- **Footprint is in metres** and converted to grid cells at `0.5 m/cell`
  (a 4 m × 3 m stepper → 8 × 6 cells). A header literally saying “cell” skips
  the conversion.
- **Machine types are resolved** by a hand-curated alias table (`stepper` →
  `LITHO_SCANNER`, `furnace` → `DIFFUSION_FURNACE`, …), then a fuzzy fallback.
  An unrecognised type is **surfaced in the table** (amber) and kept — never
  silently misclassified.
- Rows missing watts or dB are skipped with a line-numbered warning; the parser
  stops at the first fully-blank row.

### Pipeline

```
Uploaded .xlsx/.csv
      │  core/equipment_parser.parse_upload()   → EquipmentDraft[] (no coords)
      ▼
POST /equipment/parse  →  POST /equipment/place  →  POST /simulate
      │                     core/placement.auto_place()          (unchanged chain)
      ▼
EquipmentItem[] (grid coords)  ─→  run_correction()  ─→  final_layout + spatial
```

Nothing about the corrector, providers, or spatial renderer changes — an
uploaded machine is just another `EquipmentItem` by the time it reaches them.

### Placement strategies (`core/placement.py`)

| Strategy | What it does |
|---|---|
| `grid` | Shelf-packs machines row-major from the top-left. Predictable default. |
| `load_based` | Hottest machines first, so they land at the edges; coolest settle later. |
| `random` | Seeded by a hash of the upload, so the same file always lands the same way. |

Placement is only the **starting** layout — the corrector is free to move
everything afterwards. When a fab genuinely has more footprint than fits,
extra drafts are dropped (the endpoint returns `422` if none fit).

### Endpoints

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/v1/equipment/parse` | multipart `file` | `EquipmentUpload` (drafts + room + summary) |
| `POST /api/v1/equipment/place` | `{ upload, strategy, room? }` | `PlacementResult` (positioned `EquipmentItem[]`) |
| `POST /api/v1/simulate/upload` | multipart `file` + `?strategy=` | full `SimulateResponse` (identical shape to `/simulate`) |

Uploads are capped at **5 MB** (`413` beyond that), non-xlsx/csv types return
`415`, and unparseable files return `422`.

### Deliberately out of scope (hackathon tier)

- **DOCX / PDF** parsing (incl. scanned PDFs). XLSX + CSV cover the stated
  real-world case (operators keep these in spreadsheets).
- **Persistent history** of uploads. Runs stay stateless; re-uploading is cheap.



| Mode | How you enter it | What runs |
|---|---|---|
| **Autonomous** | `▸ Run simulation` with a seed | generator → corrector → canvas |
| **Co-pilot** | `✎ Edit layout`, place machines, then `🤖 Compute suggestions` | your layout → same corrector → canvas |
| **Manual** | drag only, never click the agent | FDM + vibration only, **zero LLM calls** |

Three endpoints back it:

| Endpoint | Throttle scope | LLM? | Purpose |
|---|---|---|---|
| `GET /api/v1/equipment/catalog` | `catalog` (120/min) | no | the 8 draggable machine types |
| `POST /api/v1/simulate/synopsis` | `preview` (60/min) | **never** | live physics verdict while dragging |
| `POST /api/v1/simulate` | `simulate` (12/min) | yes | full agentic correction |

Why a separate preview scope matters: dragging a machine must cost one FDM
solve (`~50 ms`), not one LLM round-trip. Sharing the `simulate` bucket would
let a user burn the corrector quota just by nudging a box around the canvas.

The catalog is generated from `core/synthetic_gen.CATALOG`, so a machine you
drag in is numerically identical to the same machine the generator would have
placed — one source of truth, checked by
`test_equipment_catalog_endpoint_matches_generator_catalog`.

Editor behaviour: drops snap to integer cells; a collision slides the machine
outward to the nearest free cell; an impossible drop is refused with a reason;
`Del` removes the selection; out-of-bounds input is a `422`, never a `500`.
Layout state lives in memory only — no `localStorage`, per invariant #1.

## Design invariants

1. **Zero persistence** — no DB, no `manage.py`, no `localStorage`.
2. **In-RAM simulation** — FDM heat equation solved per request.
3. **Bounded agentic loop** — ≤3 correction turns, physics-only fallback if LLMs fail.
4. **CORS explicitly allowlisted** — Vercel origin only, no wildcards.
5. **No `git init`** — `.gitignore` only.

## Deployment

### Backend → Modal

`modal` is a **dev-time** dependency only. It lives in
`[project.optional-dependencies].dev` and is deliberately absent from
`requirements.txt`: the deploy image is built *by* Modal, so shipping the client
inside it would be ~80 MB of dead weight.

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -e ".[dev]"                            # includes modal
modal token new                                    # browser flow, one-time
modal profile current                              # confirm the workspace
```

> `modal_app.py` imports cleanly without credentials, but no Modal command
> works until a token is present. Verify with `modal token info`.

Then deploy:

```bash
modal secret create llm-keys \
    META_LLAMA_KEY=... \
    META_MODEL_ID=meta-muse-wonder-2026 \
    META_IMAGE_MODEL_ID=meta-muse-image-2026 \
    FIREWORKS_API_KEY=... \
    FIREWORKS_MODEL_ID=accounts/fireworks/models/muse-spark-open \
    FIREWORKS_READ_TIMEOUT_S=45 \
    FIREWORKS_MAX_TOKENS=4096 \
    FIREWORKS_REASONING_EFFORT=low \
    FIREWORKS_IMAGE_MODEL_ID=accounts/fireworks/models/muse-spark-open \
    CORS_ALLOWED_ORIGINS=https://<your-app>.vercel.app

modal deploy backend/modal_app.py
```

> **Do not omit `FIREWORKS_MAX_TOKENS` and `FIREWORKS_REASONING_EFFORT`.** The
> default model (`muse-spark-open`) is a *reasoning* model: at the old 1024-token
> cap it spends the entire budget thinking and returns empty content
> (`finish_reason=length`). 4096 + `reasoning_effort=low` leaves ~640 tokens of
> thinking plus room for the JSON reply. Re-run `scripts/list_fireworks_models.py`
> before shipping, since Fireworks rotates its catalogue. If you swap to a
> chat-instruct model, set `FIREWORKS_MAX_TOKENS=1024` and
> `FIREWORKS_REASONING_EFFORT=` (explicitly empty).

#### What Modal actually does (and does not do)

Modal is the **runtime host**, not a compute or inference service:

| Modal's job | Not Modal's job |
|---|---|
| Build the container image from `requirements.txt` + `app/ api/ agents/ core/` | Run the FDM solve — that is *our* NumPy code, inside the container |
| Inject `modal.Secret` values into `os.environ` at startup | Serve Meta or Fireworks models — we call them over HTTPS with `httpx` |
| Terminate TLS at `https://<org>--farbarchitect-ai-web.modal.run` | Store anything — there is no DB, no volume, no Redis |
| Keep containers warm (`@modal.concurrent(max_inputs=8)`) | Decide layout corrections — the LLM proposes, our solver verifies |

Two commands, different lifetimes:

| | `modal serve backend/modal_app.py` | `modal deploy backend/modal_app.py` |
|---|---|---|
| Runs where | ephemeral Modal containers (cloud) | persistent Modal deployment |
| URL | `…-dev.modal.run`, rotates | stable `…modal.run` |
| Lifetime | dies on Ctrl+C | until redeployed |
| Hot reload | yes, on file save | no |
| Cost | cloud compute while running | cloud compute per request |
| Use for | pre-deploy smoke test | the demo |

> `modal serve` is **not** a local execution mode — it builds and runs in
> Modal's cloud and generates a `-dev` URL. It blocks the terminal (there is no
> `--detach` in SDK 1.6.3), so use a second shell for queries.

### Frontend → Vercel

```bash
cd frontend
vercel env add VITE_BACKEND_URL production --value https://<org>--farbarchitect-ai-web.modal.run
vercel deploy --prod
```

See `SECURITY.md` for the hardened runtime posture.

