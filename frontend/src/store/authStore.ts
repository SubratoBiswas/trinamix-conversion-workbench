import { create } from "zustand";
import type { User } from "@/types";

interface AuthState {
  user: User | null;
  token: string | null;
  setAuth: (token: string, user: User) => void;
  clear: () => void;
  hydrate: () => void;
}

// Eagerly read from localStorage so ProtectedRoute never sees a false null on reload
const _storedToken = localStorage.getItem("trinamix.token");
const _storedUser = (() => {
  try { const r = localStorage.getItem("trinamix.user"); return r ? JSON.parse(r) : null; }
  catch { return null; }
})();

export const useAuth = create<AuthState>((set) => ({
  user: _storedUser,
  token: _storedToken,
  setAuth: (token, user) => {
    localStorage.setItem("trinamix.token", token);
    localStorage.setItem("trinamix.user", JSON.stringify(user));
    set({ token, user });
  },
  clear: () => {
    localStorage.removeItem("trinamix.token");
    localStorage.removeItem("trinamix.user");
    set({ token: null, user: null });
  },
  hydrate: () => {
    const token = localStorage.getItem("trinamix.token");
    const userRaw = localStorage.getItem("trinamix.user");
    if (token && userRaw) {
      try {
        set({ token, user: JSON.parse(userRaw) });
      } catch { /* ignore */ }
    }
  },
}));
