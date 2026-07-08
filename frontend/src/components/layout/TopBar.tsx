import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, LogOut, Plus, Cpu } from "lucide-react";
import { useAuth } from "@/store/authStore";
import { SettingsApi, type AiModelOption } from "@/api";

export const TopBar: React.FC = () => {
  const { user, clear } = useAuth();
  const nav = useNavigate();

  // Global Anthropic model selector — lets the user trade cost vs capability
  // (Haiku = cheapest/fastest → Opus = most capable/priciest). Applies to every
  // AI feature (mapping, crosswalks, defaults, data-quality, error explanations).
  const [models, setModels] = useState<AiModelOption[]>([]);
  const [model, setModel] = useState<string>("");
  const [savingModel, setSavingModel] = useState(false);

  useEffect(() => {
    SettingsApi.getAiModel()
      .then((r) => { setModels(r.options); setModel(r.current); })
      .catch(() => { /* AI settings unavailable — hide selector */ });
  }, []);

  const changeModel = async (id: string) => {
    const prev = model;
    setModel(id);
    setSavingModel(true);
    try { const r = await SettingsApi.setAiModel(id); setModel(r.current); }
    catch { setModel(prev); }
    finally { setSavingModel(false); }
  };

  const logout = () => {
    clear();
    nav("/login");
  };

  const activeTier = models.find((m) => m.id === model)?.tier;

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
        {models.length > 0 && (
          <div
            className="hidden md:flex items-center gap-1.5 rounded-md border border-line bg-canvas px-2 py-1"
            title={
              activeTier
                ? `AI model: ${activeTier}. Lower = cheaper & faster, higher = more capable. Controls token spend across all AI features.`
                : "Choose the Anthropic model used for all AI features"
            }
          >
            <Cpu className={"h-3.5 w-3.5 text-brand" + (savingModel ? " animate-pulse" : "")} />
            <select
              value={model}
              onChange={(e) => changeModel(e.target.value)}
              disabled={savingModel}
              className="max-w-[9.5rem] bg-transparent text-xs font-medium text-ink focus:outline-none disabled:opacity-60"
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </select>
          </div>
        )}
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
