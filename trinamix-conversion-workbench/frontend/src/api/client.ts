import axios from "axios";
import { useAuth } from "@/store/authStore";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ? `${import.meta.env.VITE_API_URL}/api` : "/api",
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
      // Log which URL caused the 401 so we can debug
      const failedUrl = err?.config?.url ?? "unknown";
      const tok = localStorage.getItem("trinamix.token");
      localStorage.setItem(
        "trinamix.last401",
        JSON.stringify({ url: failedUrl, hadToken: !!tok, ts: Date.now() })
      );
      console.error("[auth] 401 on", failedUrl, "| had token:", !!tok);
      useAuth.getState().clear();
    }
    return Promise.reject(err);
  }
);
