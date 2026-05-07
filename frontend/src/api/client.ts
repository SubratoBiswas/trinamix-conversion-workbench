import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
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
      // Avoid hard redirect loops if already on /login
      if (!location.pathname.startsWith("/login")) location.href = "/login";
    }
    return Promise.reject(err);
  }
);
