import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UploadCloud, Sparkles, ArrowRight, Check, FileSpreadsheet, GraduationCap, Loader2 } from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi, ProjectsApi, SourceSystemsApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import type { FBDITemplate, Project } from "@/types";

const pct = (n: number) => `${Math.round((n || 0) * 100)}%`;
const confTone = (n: number) => (n >= 0.6 ? "success" : n >= 0.3 ? "warning" : "neutral");

/**
 * Guided "Convert a file" flow:
 *   upload → AI detects source system + target FBDI template (editable) →
 *   create conversion → hand off to AI column mapping → FBDI output.
 */
export const ConvertFilePage: React.FC = () => {
  const nav = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [sources, setSources] = useState<{ code: string; display_name: string }[]>([]);
  const [templates, setTemplates] = useState<FBDITemplate[]>([]);

  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [dataset, setDataset] = useState<any | null>(null);
  const [cls, setCls] = useState<Awaited<ReturnType<typeof DatasetsApi.classify>> | null>(null);
  const [sourceSystem, setSourceSystem] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ProjectsApi.list().then((ps) => { setProjects(ps); if (ps[0]) setProjectId(ps[0].id); }).catch(() => {});
    SourceSystemsApi.list().then(setSources).catch(() => setSources([]));
    FbdiApi.list().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  const templateName = useMemo(
    () => templates.find((t) => t.id === templateId),
    [templates, templateId],
  );

  const analyze = async () => {
    if (!file) return;
    setBusy(true); setError(null); setCls(null); setDataset(null);
    try {
      const ds = await DatasetsApi.upload(file, file.name.replace(/\.[^.]+$/, ""));
      setDataset(ds);
      const c = await DatasetsApi.classify((ds as any).id);
      setCls(c);
      setSourceSystem(c.source.detected || "custom");
      setTemplateId(c.target.detected_template_id || "");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Could not analyze the file. Check the format (CSV/XLSX) and try again.");
    } finally { setBusy(false); }
  };

  const createAndMap = async () => {
    if (!dataset || !projectId || !templateId) return;
    setCreating(true); setError(null);
    const targetObject = templateName?.business_object || templateName?.name;
    try {
      // Remember the confirmed choice so future similar files auto-recommend.
      await DatasetsApi.classifyLearn((dataset as any).id, {
        source_system: sourceSystem, template_id: templateId, target_object: targetObject || undefined,
      }).catch(() => {});
      const conv = await ConversionsApi.create({
        project_id: projectId,
        name: (dataset as any).name || file?.name || "File conversion",
        dataset_id: (dataset as any).id,
        template_id: templateId,
        target_object: targetObject,
        source_type: "dataset",
        status: "draft",
      } as any);
      nav(`/mappings?conversion=${(conv as any).id}`);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Could not create the conversion.");
      setCreating(false);
    }
  };

  const step = !dataset ? 1 : 2;

  return (
    <>
      <PageTitle
        title="Convert a file"
        subtitle="Upload a source extract — AI detects its source system and the matching Fusion FBDI target, then hands off to mapping."
      />

      {/* Flow hint */}
      <div className="mb-4 flex flex-wrap items-center gap-2 text-[11px] text-ink-muted">
        {["Upload file", "AI detects source & target", "Map columns", "Download FBDI"].map((s, i) => (
          <React.Fragment key={s}>
            <span className={`rounded-full px-2.5 py-1 ${i < step ? "bg-brand-subtle font-medium text-brand-dark" : "border border-line bg-white"}`}>{i + 1}. {s}</span>
            {i < 3 && <ArrowRight className="h-3 w-3 text-ink-subtle" />}
          </React.Fragment>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-danger/40 bg-danger-subtle/50 px-4 py-3 text-[12.5px] text-danger">{error}</div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Step 1 — upload */}
        <Card>
          <CardHeader title="1 · Source file" subtitle="CSV or Excel export from any source system" />
          <CardBody className="space-y-3">
            <div>
              <label className="label">Engagement</label>
              <select className="input" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}{p.client ? ` · ${p.client}` : ""}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Source file</label>
              <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-line bg-canvas px-4 py-4 hover:border-brand">
                <UploadCloud className="h-5 w-5 text-brand" />
                <span className="text-sm text-ink">{file ? file.name : "Choose a CSV / XLSX file to convert"}</span>
                <input type="file" accept=".csv,.xlsx,.xls" className="hidden"
                  onChange={(e) => { setFile(e.target.files?.[0] || null); setDataset(null); setCls(null); }} />
              </label>
            </div>
            <Button onClick={analyze} loading={busy} disabled={!file || !projectId}>
              <Sparkles className="h-4 w-4" /> Upload & analyze
            </Button>
          </CardBody>
        </Card>

        {/* Step 2 — AI classification (editable) */}
        <Card>
          <CardHeader
            title="2 · AI classification"
            subtitle="Auto-detected — override if needed"
            actions={cls?.learned ? <Pill tone="brand"><GraduationCap className="mr-1 h-3 w-3" />from learning</Pill> : undefined}
          />
          <CardBody className="space-y-3">
            {!cls ? (
              <div className="flex items-center gap-2 py-6 text-sm text-ink-muted">
                {busy ? <><Loader2 className="h-4 w-4 animate-spin" /> Analyzing the file…</> : <><FileSpreadsheet className="h-4 w-4" /> Upload a file to see the detected source and target.</>}
              </div>
            ) : (
              <>
                <div>
                  <label className="label flex items-center justify-between">
                    <span>Source system</span>
                    {cls.source.candidates[0] && (
                      <Pill tone={confTone(cls.source.candidates[0].confidence)}>
                        AI {pct(cls.source.candidates[0].confidence)}
                      </Pill>
                    )}
                  </label>
                  <select className="input" value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)}>
                    {sources.map((s) => <option key={s.code} value={s.code}>{s.display_name}</option>)}
                  </select>
                  {cls.source.candidates[0]?.reason && (
                    <p className="mt-1 text-[11px] text-ink-subtle">Why: {cls.source.candidates[0].reason}</p>
                  )}
                </div>

                <div>
                  <label className="label flex items-center justify-between">
                    <span>Target FBDI template</span>
                    {cls.target.suggestions[0] && (
                      <Pill tone={confTone(cls.target.suggestions[0].confidence)}>
                        AI {pct(cls.target.suggestions[0].confidence)}
                      </Pill>
                    )}
                  </label>
                  <select className="input" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                    <option value="">— choose a target —</option>
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>{t.business_object || t.name}{t.business_object ? ` · ${t.name}` : ""}</option>
                    ))}
                  </select>
                  {cls.target.suggestions.length > 0 && (
                    <p className="mt-1 text-[11px] text-ink-subtle">
                      Suggested: {cls.target.suggestions.slice(0, 3).map((s) => `${s.business_object} (${pct(s.confidence)})`).join(", ")}
                    </p>
                  )}
                </div>

                <Button onClick={createAndMap} loading={creating} disabled={!templateId || !projectId} className="w-full">
                  <Check className="h-4 w-4" /> Create conversion & start mapping
                </Button>
                <p className="text-[11px] text-ink-subtle">
                  Your choice is saved so the next file with the same columns is recommended automatically. After you approve the mapping, download the FBDI file from Output Preview to upload into Fusion.
                </p>
              </>
            )}
          </CardBody>
        </Card>
      </div>
    </>
  );
};
