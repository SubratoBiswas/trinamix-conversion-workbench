import React from "react";
import { Link, useLocation } from "react-router-dom";
import { ShieldOff } from "lucide-react";
import { useAuth } from "@/store/authStore";

/**
 * 403. Shown when a signed-in user opens a route their role does not allow.
 *
 * Deliberately NOT a redirect to Home. A silent bounce is indistinguishable from
 * a broken link, and the user tries again — the same shape as the blank page
 * that could not say why. This says which account is signed in, what it would
 * take to change that, and gives one way out.
 *
 * It is also careful not to imply the sign-in was the problem. 401 and 403 are
 * different answers: signing out and back in fixes the first and wastes the
 * user's time on the second.
 */
export const ForbiddenPage: React.FC = () => {
  const user = useAuth((s) => s.user);
  const { pathname } = useLocation();

  return (
    <div className="mx-auto max-w-lg py-16 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-canvas">
        <ShieldOff className="h-5 w-5 text-ink-muted" />
      </div>
      <h1 className="text-xl font-semibold text-ink">This section is for administrators</h1>
      <p className="mt-2 text-sm text-ink-muted">
        Your account does not have access to{" "}
        <span className="font-mono text-ink">{pathname}</span>. You are signed in as{" "}
        <span className="font-medium text-ink">{user?.email || "an unknown account"}</span>
        {user?.role ? <> with the <span className="font-medium text-ink">{user.role}</span> role</> : null}.
      </p>
      <p className="mt-2 text-sm text-ink-muted">
        Signing out and back in will not change this — ask an administrator to
        change your role if you need it.
      </p>
      <Link to="/" className="btn btn-primary mt-6 inline-flex">
        Back to Home
      </Link>
    </div>
  );
};

export default ForbiddenPage;
