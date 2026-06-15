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

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("trinamix.token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem("trinamix.token");
      localStorage.removeItem("trinamix.user");
      if (!location.pathname.startsWith("/login")) location.href = "/login";
    }
    return Promise.reject(err);
  }
);
