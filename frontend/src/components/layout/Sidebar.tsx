import React, { useCallback, useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  Home, Database, FileSpreadsheet, Boxes, Workflow as WfIcon,
  Sparkles, ListChecks, ShieldCheck, Cloud, Network, BookOpen,
  Library, ArrowLeftRight, BadgeCheck, Eye, AlertTriangle, Layers,
  CircleDot, Activity, Award, Building2, UsersRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ConversionsApi, ProjectsApi } from "@/api";
import { ADMIN_ONLY_SECTIONS, isAdmin } from "@/lib/access";
import { useAuth } from "@/store/authStore";

interface NavItem { to: string; label: string; icon: React.ElementType; badge?: number; }
interface NavGroup { label: string; items: NavItem[]; }

const GROUPS: NavGroup[] = [
  {
    label: "Overview",
    items: [
      { to: "/", label: "Home", icon: Home },
    ],
  },
  {
    label: "Datasets",
    items: [
      { to: "/datasets", label: "All Datasets", icon: Database },
    ],
  },
  {
    label: "FBDI Library",
    items: [
      { to: "/fbdi", label: "Templates", icon: FileSpreadsheet },
      { to: "/gold", label: "Gold Standards", icon: Award },
      { to: "/fbdi?tab=targets", label: "Target Objects", icon: BadgeCheck },
    ],
  },
  {
    label: "Conversion Workbench",
    items: [
      { to: "/clients", label: "Clients", icon: Building2 },
      { to: "/projects", label: "Projects", icon: Boxes },
      { to: "/conversions", label: "Conversion Objects", icon: Layers },
      { to: "/workflows", label: "Dataflows", icon: WfIcon },
      { to: "/mappings", label: "Mapping Review", icon: ArrowLeftRight },
      { to: "/recommendations", label: "Recommendations", icon: Sparkles },
      { to: "/output", label: "Output Preview", icon: Eye },
    ],
  },
  {
    label: "Load Management",
    items: [
      { to: "/cutover", label: "Migration Monitor", icon: Activity },
      { to: "/load", label: "Load Runs", icon: Cloud },
      { to: "/load/errors", label: "Error Traceback", icon: AlertTriangle },
      { to: "/dependencies", label: "Dependency Graph", icon: Network },
    ],
  },
  {
    label: "AI Engine",
    items: [
      { to: "/learning", label: "Learning Center", icon: BookOpen },
      { to: "/mapping-documents", label: "Mapping Documents", icon: FileSpreadsheet },
      { to: "/rules", label: "Rule Library", icon: Library },
      { to: "/dq-rules", label: "Validation & Cleansing", icon: ShieldCheck },
      { to: "/crosswalks", label: "Crosswalk Library", icon: ArrowLeftRight },
    ],
  },
  {
    label: "Governance",
    items: [
      { to: "/audit", label: "Audit Trail", icon: ShieldCheck },
      { to: "/approvals", label: "Approvals", icon: ListChecks },
    ],
  },
  {
    label: "Administration",
    items: [
      { to: "/users", label: "Users", icon: UsersRound },
    ],
  },
];

const SidebarItem: React.FC<{ item: NavItem }> = ({ item }) => {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      className={({ isActive }) =>
        cn(
          "group flex items-center gap-2.5 rounded-md px-3 py-1.5 text-[12.5px] font-medium transition",
          isActive
            ? "bg-brand text-white shadow-sm"
            : "text-slate-300 hover:bg-sidebar-hover hover:text-white"
        )
      }
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="flex-1 truncate">{item.label}</span>
      {typeof item.badge === "number" && (
        <span className="rounded-full bg-danger px-1.5 py-0.5 text-[10px] font-semibold text-white">{item.badge}</span>
      )}
    </NavLink>
  );
};

export const Sidebar: React.FC = () => {
  const [activeCount, setActiveCount] = useState<{
    projects: number; conversions: number;
  } | null>(null);
  const loc = useLocation();
  // Presentation only. Every one of these screens is also guarded on its API
  // route, which is where the decision is actually enforced — see lib/access.ts.
  // Hiding the link stops a Normal user being shown a door that will not open;
  // it is not what keeps them out.
  const user = useAuth((s) => s.user);
  const groups = isAdmin(user)
    ? GROUPS
    : GROUPS.filter((g) => !ADMIN_ONLY_SECTIONS.includes(g.label));

  const load = useCallback(() => {
    Promise.all([
      ProjectsApi.list(),
      ConversionsApi.list(),
    ]).then(([ps, cs]) => {
      const activeProjects = ps.filter(
        (p) => p.status === "in_progress" || p.status === "planning"
      ).length;
      setActiveCount({ projects: activeProjects, conversions: cs.length });
    }).catch(() => { /* keep last known counts */ });
  }, []);

  // Refresh on mount, on every navigation, and whenever data changes elsewhere
  // (e.g. a project/conversion delete fires a 'workbench:refresh' event).
  useEffect(() => { load(); }, [load, loc.pathname]);
  useEffect(() => {
    const h = () => load();
    window.addEventListener("workbench:refresh", h);
    return () => window.removeEventListener("workbench:refresh", h);
  }, [load]);

  return (
    <aside className="flex h-full w-[260px] shrink-0 flex-col bg-sidebar text-slate-300">
      {/* Brand block */}
      <div className="px-4 pb-4 pt-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand">
            <Sparkles className="h-4 w-4 text-white" />
          </div>
          <div>
            <div className="text-[15px] font-bold leading-tight text-white">Trinamix</div>
            <div className="text-[10.5px] uppercase tracking-wider text-slate-500">Conversion Workbench</div>
          </div>
        </div>

        {/* Status badge */}
        {activeCount && (
          <div className="mt-3 flex items-center gap-2 rounded-md border border-slate-700/40 bg-sidebar-hover/40 px-2.5 py-1.5">
            <CircleDot className="h-3 w-3 animate-pulse text-success" />
            <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-300">
              {activeCount.projects} active project{activeCount.projects === 1 ? "" : "s"}
            </span>
            <span className="text-[10.5px] text-slate-500">·</span>
            <span className="text-[10.5px] uppercase tracking-wider text-slate-500">
              {activeCount.conversions} conversion{activeCount.conversions === 1 ? "" : "s"}
            </span>
          </div>
        )}
      </div>

      {/* Nav */}
      <div className="flex-1 overflow-y-auto px-3 pb-6">
        {groups.map((g) => (
          <div key={g.label} className="mb-4">
            <div className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              {g.label}
            </div>
            <div className="space-y-0.5">
              {g.items.map((it) => <SidebarItem key={it.to + it.label} item={it} />)}
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
};
