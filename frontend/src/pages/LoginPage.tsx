import React, { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { AuthApi } from "@/api";
import { useAuth } from "@/store/authStore";
import { Button } from "@/components/ui/Primitives";

export const LoginPage: React.FC = () => {
  // Blank, deliberately. The panel below the form printed the default
  // account and its password on a public sign-in page; removing that and
  // leaving them pre-filled here would move the disclosure rather than end
  // it — the bundle still carries the string, and the form still submits
  // them on a stray Enter.
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const setAuth = useAuth((s) => s.setAuth);
  const token = useAuth((s) => s.token);
  const nav = useNavigate();

  // Already signed in (e.g. landed here from a stale link, or signed in on
  // another tab)? Don't make the user re-enter credentials they already have —
  // send them straight to the app.
  if (token) return <Navigate to="/" replace />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await AuthApi.login(email, password);
      setAuth(res.access_token, res.user);
      nav("/", { replace: true });
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Sign-in failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-sidebar p-6">
      <div className="grid w-full max-w-4xl grid-cols-1 overflow-hidden rounded-xl shadow-soft md:grid-cols-2">
        {/* Left brand */}
        <div className="hidden flex-col justify-between bg-sidebar p-10 text-slate-200 md:flex">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand">
              <Sparkles className="h-5 w-5 text-white" />
            </div>
            <div>
              <div className="text-base font-semibold text-white">Trinamix</div>
              <div className="text-xs text-slate-400">Conversion Workbench</div>
            </div>
          </div>
          <div>
            <h2 className="text-2xl font-semibold leading-tight text-white">
              AI-powered Oracle Fusion data conversion.
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-slate-400">
              Upload legacy extracts, map to FBDI templates with AI assistance,
              cleanse and validate, then simulate or trigger Fusion loads — all
              with full lineage and approval governance.
            </p>
          </div>
          <div className="text-[11px] text-slate-500">© Trinamix · Local development build</div>
        </div>

        {/* Right form */}
        <div className="flex flex-col justify-center bg-white p-10">
          <h1 className="text-xl font-semibold text-ink">Sign in</h1>
          <p className="mt-1 text-sm text-ink-muted">Use the seeded admin credentials to get started.</p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label className="label">Email</label>
              <input
                className="input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
                autoComplete="email"
                required
              />
            </div>
            <div>
              <label className="label">Password</label>
              <input
                className="input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                type="password"
                autoComplete="current-password"
                required
              />
            </div>
            {error && (
              <div className="rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-xs text-danger">
                {error}
              </div>
            )}
            <Button type="submit" loading={loading} className="w-full">
              Sign in
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
};
