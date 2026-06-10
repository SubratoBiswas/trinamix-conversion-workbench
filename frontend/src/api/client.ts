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
      // Clear auth state via Zustand — React Router's ProtectedRoute will redirect
      // to /login with state.from set, so the user lands back here after re-login.
      useAuth.getState().clear();
    }
    return Promise.reject(err);
  }
);
