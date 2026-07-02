import axios from "axios";

// In production VITE_API_URL points to the backend service (e.g. https://trinamix-backend.onrender.com).
// In local dev it is unset and the Vite proxy forwards /api → localhost:8000.
const BASE = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api`
  : "/api";

export const api = axios.create({
  baseURL: BASE,
  timeout: 60_000,
});

// ── Global API activity tracker ──────────────────────────────────────────────
// Every request through this axios instance increments an in-flight counter and
// notifies subscribers. A single root component (GlobalActivityBar) listens and
// shows a progress bar + "Working…" spinner, so ALL API calls — AI mapping, live
// EBS fetches, Fusion loads, anything — get a processing indicator for free.
let _inflight = 0;
const _listeners = new Set<(n: number) => void>();
const _notify = () => { _listeners.forEach((fn) => fn(_inflight)); };

export const apiActivity = {
  count: () => _inflight,
  subscribe(fn: (n: number) => void) {
    _listeners.add(fn);
    fn(_inflight);
    return () => { _listeners.delete(fn); };
  },
};

api.interceptors.request.use((config) => {
  _inflight += 1;
  _notify();
  const token = localStorage.getItem("trinamix.token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

const _settle = () => { _inflight = Math.max(0, _inflight - 1); _notify(); };

api.interceptors.response.use(
  (r) => { _settle(); return r; },
  async (err) => {
    const cfg = err?.config || {};
    const status = err?.response?.status;
    const method = (cfg.method || "get").toLowerCase();
    // Auto-recover from Render free-tier cold starts / brief deploy blips. A sleeping
    // backend returns a no-CORS holding page (axios sees a network error with no
    // response) or a 502/503/504 while waking. Retry idempotent GETs a few times with
    // backoff so pages self-heal instead of crashing — the first request wakes the
    // server and a later retry succeeds. Writes are NOT retried (avoids duplicates).
    const transient = !err.response || status === 502 || status === 503 || status === 504;
    // Retry idempotent GETs, plus login (safe to re-issue — it only returns a token),
    // so a cold-start timeout on the very first request doesn't fail sign-in.
    const retryable = method === "get" || (cfg.url || "").includes("/auth/login");
    if (transient && retryable && (cfg.__retry || 0) < 3) {
      cfg.__retry = (cfg.__retry || 0) + 1;
      _settle();
      await new Promise((res) => setTimeout(res, 2500 * cfg.__retry));
      return api(cfg);
    }
    _settle();
    if (status === 401) {
      localStorage.removeItem("trinamix.token");
      localStorage.removeItem("trinamix.user");
      if (!location.pathname.startsWith("/login")) location.href = "/login";
    }
    return Promise.reject(err);
  }
);

// Warm the free-tier backend the moment the app loads, so the first real request
// (usually sign-in) doesn't hit a cold-start timeout. Fire-and-forget — the GET
// retry logic above keeps pinging until the server wakes.
api.get("/health").catch(() => {});
