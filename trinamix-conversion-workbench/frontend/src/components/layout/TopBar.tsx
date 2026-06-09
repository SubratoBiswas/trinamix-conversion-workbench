import React from "react";
import { useNavigate } from "react-router-dom";
import { Search, LogOut, Plus } from "lucide-react";
import { useAuth } from "@/store/authStore";

export const TopBar: React.FC = () => {
  const { user, clear } = useAuth();
  const nav = useNavigate();

  const logout = () => {
    clear();
    nav("/login");
  };

  return (
    <div className="flex h-14 items-center gap-3 border-b border-line bg-white px-5">
      <div className="relative flex-1 max-w-2xl">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle" />
        <input
          className="h-9 w-full rounded-md border border-line bg-canvas pl-9 pr-3 text-sm text-ink placeholder:text-ink-subtle focus:border-brand focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand"
          placeholder="Search datasets, templates, projects, workflows…"
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => nav("/projects/new")}
          className="btn-primary h-9"
          title="New conversion project"
        >
          <Plus className="h-4 w-4" /> Create
        </button>
        <div className="ml-2 flex items-center gap-2 border-l border-line pl-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand text-xs font-semibold text-white">
            {(user?.name || "A").slice(0, 1).toUpperCase()}
          </div>
          <div className="hidden sm:block">
            <div className="text-xs font-semibold text-ink leading-tight">{user?.name}</div>
            <div className="text-[11px] text-ink-muted leading-tight">{user?.role}</div>
          </div>
          <button onClick={logout} className="btn-ghost h-8 px-2" title="Sign out">
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
