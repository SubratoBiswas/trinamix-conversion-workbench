import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UploadCloud, Sparkles, ArrowRight, Check, GraduationCap, Loader2, X } from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi, ProjectsApi, SourceSystemsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import type { FBDITemplate, Project } from "@/types";

const pct = (n: number) => `${Math.round((n || 0) * 100)}%`;
const confTone = (n: number) => (n >= 0.6 ? "success" : n >= 0.3 ? "warning" : "neutral");

type Item = {
  key: string;
  file: File;
  status: "pending" | "analyzing" | "ready" | "error";
  datasetId?: string;
  learned?: boolean;
  source?: string;
  sourceConf?: number;
  templateId?: string;
  targetConf?: number;
  error?: string;
};

/**
 * Guided "Convert a file" flow — supports MULTIPLE files:
 *   upload → AI detects each file's source system + target FBDI template
 *   (editable) → create one conversion per file → AI column mapping → FBDI output.
 */
export const ConvertFilePage: React.FC = () => {
  const nav = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [sources, setSources] = useState<{ code: string; display_name: string }[]>([]);
  const [templates, setTemplates] = useState<FBDITemplate[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ProjectsApi.list().then((ps) => { setProjects(ps); if (ps[0]) setProjectId(ps[0].id); }).catch(() => {});
    SourceSystemsApi.list().then(setSources).catch(() => setSources([]));
    FbdiApi.list().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  const addFiles = (fl: FileList | null) => {
    const add = Array.from(fl || []).map((file) => ({
      key: `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 7)}`,
      file, status: "pending" as const,
    }));
    if (add.length) setItems((prev) => [...prev, ...add]);
  };
  const patch = (key: string, p: Partial<Item>) =>
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...p } : it)));
  const remove = (key: string) => setItems((prev) => prev.filter((it) => it.key !== key));

  const analyzeAll = async () => {
    setAnalyzing(true); setError(null);
    for (const it of items) {
      if (it.status === "ready") continue;
      patch(it.key, { status: "analyzing", error: undefined });
      try {
        const ds: any = await DatasetsApi.upload(it.file, it.file.name.replace(/\.[^.]+$/, ""));
        const cls = await DatasetsApi.classify(ds.id);
        patch(it.key, {
          status: "ready", datasetId: ds.id, learned: cls.learned,
          source: cls.source.detected || "custom",
          sourceConf: cls.source.candidates[0]?.confidence ?? 0,
          templateId: cls.target.detected_template_id || "",
          targetConf: cls.target.suggestions[0]?.confidence ?? 0,
        });
      } catch (e: any) {
        patch(it.key, { status: "error", error: e?.response?.data?.detail || "Could not analyze this file." });
      }
    }
    setAnalyzing(false);
  };

  const createAll = async () => {
    const ready = items.filter((it) => it.status === "ready" && it.templateId && it.datasetId);
    if (!projectId || ready.length === 0) return;
    setCreating(true); setError(null);
    const proj = projects.find((p) => p.id === projectId);
    const outputMode = /fbdi/i.test((proj as any)?.target_environment || "") ? "fbdi_download" : "fusion_load";
    try {
      for (const it of ready) {
        const tpl = templates.find((t) => t.id === it.templateId);
        const targetObject = tpl?.business_object || tpl?.name;
        await DatasetsApi.classifyLearn(it.datasetId!, {
          source_system: it.source, template_id: it.templateId, target_object: targetObject || undefined,
        }).catch(() => {});
        await ConversionsApi.create({
          project_id: projectId,
          name: it.file.name.replace(/\.[^.]+$/, ""),
          dataset_id: it.datasetId, template_id: it.templateId,
          target_object: targetObject, source_type: "dataset",
          output_mode: outputMode, status: "draft",
        } as any);
      }
      nav(`/projects/${projectId}`);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Could not create conversions.");
      setCreating(false);
    }
  };

  const readyCount = items.filter((it) => it.status === "ready" && it.templateId).length;
  const analyzed = items.some((it) => it.status === "ready");

  return (
    <>
      <PageTitle
        title="Convert files"
        subtitle="Upload one or more source extracts — AI detects each file's source system and target Fusion FBDI template, then hands off to mapping and FBDI download."
      />

      <div className="mb-4 flex flex-wrap items-center gap-2 text-[11px] text-ink-muted">
        {["Upload files", "AI detects source & target", "Map columns", "Download FBDI"].map((s, i) => (
          <React.Fragment key={s}>
            <span className={`rounded-full px-2.5 py-1 ${(i === 0 && items.length) || (i === 1 && analyzed) ? "bg-brand-subtle font-medium text-brand-dark" : "border border-line bg-white"}`}>{i + 1}. {s}</span>
            {i < 3 && <ArrowRight className="h-3 w-3 text-ink-subtle" />}
          </React.Fragment>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-danger/40 bg-danger-subtle/50 px-4 py-3 text-[12.5px] text-danger">{error}</div>
      )}

      <Card className="mb-4">
        <CardBody className="space-y-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <label className="label">Engagement</label>
              <select className="input" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}{p.client ? ` · ${p.client}` : ""}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Source files</label>
              <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-line bg-canvas px-4 py-2.5 hover:border-brand">
                <UploadCloud className="h-5 w-5 text-brand" />
                <span className="text-sm text-ink">Add CSV / XLSX files (multiple allowed)</span>
                <input type="file" multiple accept=".csv,.xlsx,.xls" className="hidden"
                  onChange={(e) => { addFiles(e.target.files); e.currentTarget.value = ""; }} />
              </label>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={analyzeAll} loading={analyzing} disabled={items.length === 0 || !projectId}>
              <Sparkles className="h-4 w-4" /> Upload & analyze {items.length > 0 ? `(${items.length})` : ""}
            </Button>
            {readyCount > 0 && (
              <Button variant="primary" onClick={createAll} loading={creating}>
                <Check className="h-4 w-4" /> Create {readyCount} conversion{readyCount === 1 ? "" : "s"} & map
              </Button>
            )}
          </div>
        </CardBody>
      </Card>

      {items.length > 0 && (
        <Card>
          <CardHeader title="Files" subtitle="AI classification — override the source or target per file if needed" />
          <div className="overflow-x-auto">
            <table className="table-shell">
              <thead>
                <tr><th>File</th><th>Source system</th><th>Target FBDI template</th><th>Status</th><th></th></tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.key}>
                    <td className="max-w-[220px] truncate font-medium text-ink" title={it.file.name}>{it.file.name}</td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <select className="input !h-8 !w-auto !text-xs" disabled={it.status !== "ready"}
                          value={it.source || ""} onChange={(e) => patch(it.key, { source: e.target.value })}>
                          <option value="">—</option>
                          {sources.map((s) => <option key={s.code} value={s.code}>{s.display_name}</option>)}
                        </select>
                        {it.status === "ready" && it.sourceConf != null && (
                          <Pill tone={confTone(it.sourceConf)}>AI {pct(it.sourceConf)}</Pill>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <select className="input !h-8 !w-auto !text-xs" disabled={it.status !== "ready"}
                          value={it.templateId || ""} onChange={(e) => patch(it.key, { templateId: e.target.value })}>
                          <option value="">— choose —</option>
                          {templates.map((t) => <option key={t.id} value={t.id}>{t.business_object || t.name}</option>)}
                        </select>
                        {it.status === "ready" && it.targetConf != null && (
                          <Pill tone={confTone(it.targetConf)}>AI {pct(it.targetConf)}</Pill>
                        )}
                      </div>
                    </td>
                    <td>
                      {it.status === "analyzing" ? <span className="inline-flex items-center gap-1 text-[11px] text-ink-muted"><Loader2 className="h-3 w-3 animate-spin" /> analyzing</span>
                        : it.status === "ready" ? <Pill tone="success">{it.learned ? <><GraduationCap className="mr-1 h-3 w-3" />learned</> : "ready"}</Pill>
                        : it.status === "error" ? <Pill tone="danger" >error</Pill>
                        : <span className="text-[11px] text-ink-subtle">pending</span>}
                      {it.error && <div className="mt-0.5 max-w-[180px] truncate text-[10.5px] text-danger" title={it.error}>{it.error}</div>}
                    </td>
                    <td className="text-right">
                      <button onClick={() => remove(it.key)} className="rounded p-1 text-ink-subtle hover:bg-canvas hover:text-danger" title="Remove">
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <CardBody className="!pt-3">
            <p className="text-[11px] text-ink-subtle">
              Your source/target choices are learned by column signature, so the next similar file auto-recommends. After creating, map each conversion and download its FBDI file from Output Preview to upload into Fusion.
            </p>
          </CardBody>
        </Card>
      )}
    </>
  );
};
