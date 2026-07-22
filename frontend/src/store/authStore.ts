import { create } from "zustand";
import type { User } from "@/types";

interface AuthState {
  user: User | null;
  token: string | null;
  setAuth: (token: string, user: User) => void;
  clear: () => void;
  hydrate: () => void;
}

// Read the persisted session SYNCHRONOUSLY, at store-creation time.
//
// This used to start as {token: null} and rely on App's `useEffect(hydrate)`.
// But an effect runs AFTER the first render, and ProtectedRoute renders on that
// first pass — so it saw token===null and issued <Navigate to="/login" replace>
// before hydrate could ever run. The token was valid and sitting in
// localStorage the whole time; every hard load or F5 of a protected route still
// bounced to the sign-in screen, which looked exactly like an expired session.
// Seeding the initial state here means the very first render already knows the
// user is signed in, so refreshes and deep links stay put.
const _readSession = (): { token: string | null; user: User | null } => {
  try {
    const token = localStorage.getItem("trinamix.token");
    const userRaw = localStorage.getItem("trinamix.user");
    if (token && userRaw) return { token, user: JSON.parse(userRaw) as User };
  } catch { /* corrupt/unavailable storage — fall through to signed-out */ }
  return { token: null, user: null };
};
const _initial = _readSession();

export const useAuth = create<AuthState>((set) => ({
  user: _initial.user,
  token: _initial.token,
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
