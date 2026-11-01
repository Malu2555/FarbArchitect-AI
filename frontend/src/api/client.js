/**
 * Stateless HTTP client for the fabArchitect AI backend.
 *
 * - No third-party HTTP libs: uses the native `fetch` (Undici 8 on Node 24+).
 * - Every request is bounded by an AbortController timeout.
 * - Retries 3x with exponential backoff on transient failures.
 * - Errors are sanitized before logging (any `Bearer …` fragment is redacted).
 */

const rawBase = import.meta.env.VITE_BACKEND_URL || '';
export const API_BASE = rawBase.replace(/\/$/, '');

// Reasoning models (Muse Spark / 816B-class on Fireworks) routinely take
// 60-120 s for physics + up to 3 correction turns, so the default client
// timeout is deliberately generous. Override with VITE_SIMULATE_TIMEOUT_MS.
const DEFAULT_TIMEOUT_MS = Number(
  import.meta.env.VITE_SIMULATE_TIMEOUT_MS || 180000
);
const RETRY_WAITS_MS = [200, 600, 1500];

function withTimeout(signal, ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(new Error('request timeout')), ms);
  const relay = () => ctrl.abort(signal?.reason);
  if (signal) signal.addEventListener('abort', relay, { once: true });
  return {
    signal: ctrl.signal,
    dispose() {
      clearTimeout(timer);
      if (signal) signal.removeEventListener('abort', relay);
    },
  };
}

function redact(text) {
  return String(text || '').replace(
    /Bearer\s+[A-Za-z0-9._\-+/=]+/gi,
    'Bearer [redacted]'
  );
}

/** Typed HTTP error so callers can branch on status / rate-limit metadata. */
export class ApiError extends Error {
  constructor(message, { status = 0, code = '', retryAfterSeconds = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }

  get isRateLimited() {
    return this.status === 429 || this.code === 'E_RATE_LIMITED';
  }
}

async function request(path, options = {}) {
  const {
    method = 'POST',
    body,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    signal,
  } = options;

  const url = `${API_BASE}${path}`;
  const { signal: effectiveSignal, dispose } = withTimeout(signal, timeoutMs);

  try {
    const res = await fetch(url, {
      method,
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: effectiveSignal,
    });

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      const safe = redact(text).slice(0, 240);

      // Our backend emits a structured 429 body — try to use it.
      let code = '';
      let retryAfterSeconds = null;
      try {
        const parsed = JSON.parse(safe);
        code = parsed?.code ?? '';
        retryAfterSeconds = parsed?.retry_after_seconds ?? null;
      } catch {
        /* non-JSON error body — fall back to the raw text */
      }

      if (res.status === 429) {
        const wait = retryAfterSeconds ? ` (retry in ${retryAfterSeconds}s)` : '';
        throw new ApiError(`Rate limit exceeded${wait}.`, {
          status: res.status,
          code: code || 'E_RATE_LIMITED',
          retryAfterSeconds,
        });
      }

      throw new ApiError(
        `HTTP ${res.status}${safe ? `: ${safe}` : ''}`,
        { status: res.status, code }
      );
    }
    return await res.json();
  } finally {
    dispose();
  }
}

/**
 * Only network errors and 5xx are retried. A 429 or a 4xx is deterministic and
 * retrying would just burn the caller's remaining quota.
 */
function isRetryable(err) {
  if (!(err instanceof ApiError)) return true; // abort / network / timeout
  return err.status === 0 || err.status >= 500;
}

export async function simulate({ seed = null, equipment = null } = {}) {
  const payload = {};
  if (Number.isInteger(seed)) payload.seed = seed;
  if (Array.isArray(equipment)) payload.equipment = equipment;

  let lastErr;
  for (let attempt = 0; attempt <= RETRY_WAITS_MS.length; attempt++) {
    try {
      return await request('/api/v1/simulate', { body: payload });
    } catch (err) {
      lastErr = err;
      if (!isRetryable(err) || attempt >= RETRY_WAITS_MS.length) break;
      await new Promise((r) => setTimeout(r, RETRY_WAITS_MS[attempt]));
    }
  }
  throw lastErr;
}

export async function health() {
  return request('/api/v1/health', { method: 'GET', timeoutMs: 8000 });
}

/**
 * Physics-only verdict for a user-built layout (scenario editor live preview).
 *
 * Never touches an LLM, so the timeout is short and it can be called on every
 * drag settle. The caller is responsible for debouncing.
 */
export async function synopsis({ equipment = [], signal = null } = {}) {
  const payload = { equipment: Array.isArray(equipment) ? equipment : [] };
  return request('/api/v1/simulate/synopsis', {
    body: payload,
    timeoutMs: 20000,
    signal,
  });
}

/** Draggable machine types for the scenario editor catalog. */
export async function equipmentCatalog() {
  return request('/api/v1/equipment/catalog', { method: 'GET', timeoutMs: 8000 });
}
