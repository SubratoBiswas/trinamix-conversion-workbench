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
  (err) => {
    _settle();
    if (err?.response?.status === 401) {
      localStorage.removeItem("trinamix.token");
      localStorage.removeItem("trinamix.user");
      if (!location.pathname.startsWith("/login")) location.href = "/login";
    }
    return Promise.reject(err);
  }
);
