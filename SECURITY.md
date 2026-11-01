# SECURITY — fabArchitect AI

Hardened runtime posture for the hackathon prototype. Every claim below is
backed by an explicit code path.

## 1. Toolchain

| Component | Minimum | Notes |
|---|---|---|
| Node | **26.0.0** | `engines` + `engine-strict=true` + predev/prebuild guard |
| npm | 11.0.0 | `npm ci` for hash-locked installs |
| Python | 3.11 | verified on 3.13.3 |
| OpenSSL | 3.5.9 (via Node 26) | TLS 1.3 floor, no legacy ciphers |

## 2. Transport

- **Outbound**: `httpx` (backend) and native `fetch` (frontend) over TLS 1.3.
- **CORS**: explicit allowlist via `CORS_ALLOWED_ORIGINS` (never `*`).
  Preflight `OPTIONS` handled by `core.middleware.CorsMiddleware` with
  `Access-Control-Max-Age: 86400`.
- **Frontend dev server**: binds `127.0.0.1` with `strictPort: true`.

## 3. Secrets

- **Local**: `backend/.env` (gitignored). Loader only calls
  `os.environ.setdefault`, so platform-injected values always win.
- **Production**: `modal.Secret.from_name("llm-keys")` — never bundled into
  the frontend, never logged, never returned in API responses.
- **Frontend**: only `VITE_BACKEND_URL` is exposed (public URL, no tokens).

## 4. Sandboxing

- **Frontend dev server**: binds `127.0.0.1` with `strictPort: true`, so it is
  never accidentally exposed on a LAN interface.
- `ignore-scripts=true` in `frontend/.npmrc` neutralizes supply-chain
  `postinstall` hook attacks. There are no native-module packages in the tree,
  so skipping scripts has zero functional cost.
- `engine-strict=true` + `predev`/`prebuild` guards refuse to run under any
  Node version below 26.
- Modal function runs in an ephemeral, single-purpose container with a
  180-second timeout and bounded input concurrency.
- **Known limitation (documented, not hidden):** Node 26's Permission System
  (`--permission`) is **not** applied to the Vite 5 dev server. Vite 5's
  `searchForWorkspaceRoot` probes parent directories up to the drive root,
  which would require granting `--allow-fs-read=<drive>:\\` and thus defeat
  the isolation. This is a Vite 5 limitation; a future Vite release that
  avoids the parent-directory probe can be wrapped with:
  `node --permission --allow-addons --allow-fs-read=. --allow-child-process ...`
  (evaluated during the build; it fails on Vite 5's workspace-root probe).
  The production build is a static asset bundle, so the dev-time sandbox has
  no bearing on the deployed attack surface.

## 5. Rate limiting

Implemented with Django Ninja's built-in throttles (`api/throttling.py`) — no
new dependencies, and counters live in Django's in-process `LocMemCache`.

| Endpoint | Scope | Default |
|---|---|---|
| `POST /api/v1/simulate` | `simulate` | `12/min` per IP |
| `POST /api/v1/simulate/synopsis` | `preview` | `60/min` per IP |
| `GET /api/v1/equipment/catalog` | `catalog` | `120/min` per IP |
| `GET /api/v1/health` | `health` | `120/min` per IP |
| `GET /api/v1/health/deep` | `health_deep` | `30/min` per IP |

The `preview` scope exists so the scenario editor's live drag physics can never
consume the `simulate` budget: a preview runs one FDM solve and **zero** LLM
calls, so it deserves a far larger ceiling than the corrector endpoint.

Details:

- **Separate scopes.** The stock `AnonRateThrottle` hard-codes
  `scope="anon"`, which would make every endpoint share one counter bucket.
  `ScopedAnonThrottle` assigns a per-endpoint scope so budgets are independent.
- **Env-tunable.** `RATE_SIMULATE` / `RATE_HEALTH` / `RATE_HEALTH_DEEP` /
  `RATE_ANON`. Malformed values fall back to the safe default with a log
  warning instead of crashing the container at import time.
- **Rate normalization.** Django Ninja's `parse_rate` only accepts short
  suffixes (`s`, `min`, `hour`, `day`); `12/minute` raises `ValueError`.
  `normalize_rate` accepts both spellings and canonicalizes them.
- **Client identity.** `NINJA_NUM_PROXIES` is set from `TRUSTED_PROXY_HOPS`
  (default `1`) so the real client IP is read from `X-Forwarded-For` behind
  Modal's proxy. Locally, with no proxy header, it falls back to
  `REMOTE_ADDR`.
- **Structured 429.** A custom exception handler returns
  `{detail, code: "E_RATE_LIMITED", retry_after_seconds}` plus a
  `Retry-After` header. `retry-after` is added to `CORS_EXPOSE_HEADERS` so the
  SPA can read it cross-origin.
- **Frontend behaviour.** `src/api/client.js` raises a typed `ApiError`; 429
  and other 4xx responses are **not** retried (retrying would burn the
  remaining quota), while network errors and 5xx keep the 3-attempt backoff.
- **Honest limitation.** `LocMemCache` is per process, so limits are enforced
  per container rather than globally. A shared store (Redis) would give a true
  global ceiling but would reintroduce external state, which this project
  deliberately avoids. With N warm containers the effective ceiling is
  approximately N × the configured rate.

## 6. Dual-Muse spatial validation

The layout that reaches the canvas has been geometrically verified twice:

1. **Before** any model sees it — `agents/spatial_renderer.sanitize_layout`
   clamps every footprint into `[0, 100-w] × [0, 100-h]` and pushes apart any
   overlapping pair. This is deterministic and cannot fail.
2. **After** Muse Image replies — `_parse_muse_reply` re-runs the same
   `validate_layout` checker over the model's output. If the model introduces
   an overlap, resizes a machine, or references an unknown id, the **entire
   reply is rejected** and the deterministic spec is used instead.

Guarantees enforced by tests:

- footprint sizes are immutable — a model cannot resize a machine;
- unknown ids are dropped, never invented;
- `no_overlap` and `no_clipping` are recomputed by our code, not trusted from
  the model;
- `canvas_px` is fixed at `grid_size * cell_size_px = 500` so the frontend
  canvas and the backend PNG always agree on geometry.

Provenance is surfaced as `spatial.spatial_spec.canvas.source`
(`MUSE_IMAGE` | `FALLBACK`) and echoed as `spatial_source` on the response, so
a degraded run is never silently presented as a ratified one.

The PNG preview is produced by `core/png.py`, a ~60-line encoder using only
`struct` + `zlib` from the standard library — no Pillow, no imageio, no new
dependencies.

Machine labels are rendered with `core/font5x7.py`, a bundled 5×7 bitmap font
(~45 glyphs, defined as auditable string art). Placement is adaptive: inside
the footprint when the full name fits, otherwise annotated beside the box.
`color_token` drives fill / edge / label color (`teal` / `warn` / `danger`).

## 7. Statelessness

- No database, no migrations, no `manage.py`, no browser persistence.
- All simulation state lives inside the HTTP request/response cycle and a
  bounded (30-entry) in-RAM LRU keyed by seed. There is no cross-request
  mutable state that could leak between callers.

## 6. Error hygiene

- Provider errors are classified (`E_TIMEOUT`, `E_STATUS_*`, `E_PARSE`,
  `E_SCHEMA`, `E_EMPTY_CONTENT`) and never echo raw upstream bodies to the
  client. `E_EMPTY_CONTENT` distinguishes "the model spent its budget on
  reasoning" from "the reply was truncated by `max_tokens`", so the operator
  is pointed at the env var that actually applies.
- Numeric guards assert `np.isfinite(...)` on every 10 solver steps; any
  instability raises `SolverError("E_FDM_NAN")` rather than returning NaNs.

## 7. Reporting

Please open an issue or contact the maintainers for any security concern
before public disclosure.
