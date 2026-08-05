/**
 * Which parts of the app a Normal user may see.
 *
 * PRESENTATION ONLY. Hiding a nav link is not access control — every screen here
 * is backed by an API route, and the route is where the decision is actually
 * made (backend: app/services/access_control.py). If these two lists ever
 * disagree, the backend is right and this file is a cosmetic bug; the reverse can
 * never be true. Nothing here should ever be the only thing standing between a
 * user and data.
 *
 * What it IS for: not showing somebody a door that will not open. A Normal user
 * who clicks "Gold Standards" and gets a permission error has been told the same
 * thing twice, the second time rudely.
 */
import type { User } from "@/types";

/** Sidebar groups only an administrator sees. Matched against NavGroup.label. */
export const ADMIN_ONLY_SECTIONS: readonly string[] = [
  "Datasets",
  "FBDI Library",
  "AI Engine",
  "Governance",
  "Administration",
];

/**
 * Routes only an administrator may open, as they are spelled in App.tsx.
 *
 * A Normal user reaching one of these by pasting a URL gets the 403 page rather
 * than a blank screen or a half-rendered page firing failing requests. Nav
 * filtering alone leaves that hole open, and a pasted link is exactly how
 * somebody finds it.
 *
 * `projects/new` is on this list even though Projects itself is open: the setup
 * wizard uploads datasets and manages source connections, both administrator
 * actions on the API. A Normal user who could open it would fill in four steps
 * and fail on the fifth.
 */
export const ADMIN_ONLY_PATHS: readonly string[] = [
  "convert",
  "datasets",
  "datasets/:id",
  "datasets/:id/prepare",
  "fbdi",
  "fbdi/:id",
  "gold",
  "projects/new",
  "dq-rules",
  "learning",
  "mapping-documents",
  "rules",
  "crosswalks",
  "audit",
  "approvals",
  "users",
];

/**
 * The role test, spelled the same way the backend spells it: trimmed and
 * lowercased, because the value arrives from a JWT claim and from Mongo and
 * neither guarantees the casing that was written.
 */
export const isAdmin = (user: User | null | undefined): boolean =>
  (user?.role ?? "").trim().toLowerCase() === "admin";
