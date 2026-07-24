import React, { useEffect, useMemo, useRef, useState } from "react";
import { DqRulesApi, FbdiApi, ConversionsApi, type DqRule } from "@/api";
import { Card, CardBody, CardHeader, Button, Pill, PageTitle, Spinner } from "@/components/ui/Primitives";
import type { FBDITemplate } from "@/types";

/**
 * Validation & Cleansing rule management — scoped by FBDI object + client.
 * Create rules three ways: Extract (from an FBDI template), Upload (workbook/CSV/
 * JSON), or Manual. Rules apply at Generate-time (cleanse + validate on the merged
 * output) and are exportable.
 */
export const DataQualityRulesPage: React.FC = () => {
  const [kind, setKind] = useState<"validation" | "cleansing">("validation");
  const [objectType, setObjectType] = useState<string>("");
  const [objects, setObjects] = useState<string[]>([]);
  const [templates, setTemplates] = useState<FBDITemplate[]>([]);
  const [templateId, setTemplateId] = useState<string>("");
  const [rules, setRules] = useState<DqRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string>("");
  const [adding, setAdding] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // Manual-rule form
  const [nField, setNField] = useState("");
  const [nType, setNType] = useState("REQUIRED");
  const [nParams, setNParams] = useState("");
  const [nSeverity, setNSeverity] = useState("error");

  useEffect(() => {
    (async () => {
      try {
        const ots = await ConversionsApi.objectTypes();
        // Use the interface step labels as selectable target objects.
        const objs = Array.from(new Set(ots.flatMap(o => o.steps.map(s => s.label))));
        setObjects(objs);
        if (objs.length && !objectType) setObjectType(objs[0]);
      } catch { /* ignore */ }
      try { setTemplates(await FbdiApi.list()); } catch { /* ignore */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = async () => {
    if (!objectType) return;
    setLoading(true);
    try {
      const r = await DqRulesApi.list({ target_object: objectType, kind });
      setRules(r.rules);
    } catch (e: any) { setMsg(e?.message || "Failed to load rules"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [objectType, kind]);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 4000); };

  const doExtract = async () => {
    if (!objectType || !templateId) { flash("Pick an object and an FBDI template to extract from."); return; }
    try {
      const r = await DqRulesApi.extract({ target_object: objectType, template_id: templateId });
      flash(`Extracted ${r.created} rule(s) (${r.skipped} already existed).`);
      setKind("validation"); load();
    } catch (e: any) { flash(e?.response?.data?.detail || "Extract failed"); }
  };

  const doUpload = async (f: File) => {
    try {
      const r = await DqRulesApi.upload({ kind, target_object: objectType, file: f });
      flash(`Uploaded ${r.created} ${kind} rule(s).`); load();
    } catch (e: any) { flash(e?.response?.data?.detail || "Upload failed"); }
  };

  const addManual = async () => {
    if (!nType) return;
    let params: Record<string, any> = {};
    if (nParams.trim()) {
      try { params = JSON.parse(nParams); }
      catch {
        nParams.split(";").forEach(p => { const [k, v] = p.split("="); if (k && v) params[k.trim()] = v.trim(); });
      }
    }
    try {
      await DqRulesApi.create({ kind, target_object: objectType, field: nField || null,
        rule_type: nType, params, severity: nSeverity });
      setAdding(false); setNField(""); setNParams(""); flash("Rule added."); load();
    } catch (e: any) { flash(e?.response?.data?.detail || "Add failed"); }
  };

  const toggle = async (r: DqRule) => {
    try { await DqRulesApi.update(r.id, { active: !r.active }); load(); }
    catch { flash("Update failed"); }
  };
  const remove = async (r: DqRule) => {
    try { await DqRulesApi.remove(r.id); load(); } catch { flash("Delete failed"); }
  };

  const typeOptions = kind === "cleansing"
    ? ["TRIM", "UPPERCASE", "LOWERCASE", "TITLECASE", "REMOVE_SPECIAL", "REPLACE", "DEFAULT_IF_BLANK", "PAD_LEFT"]
    : ["REQUIRED", "MAX_LENGTH", "VALUE_IN_SET", "REGEX", "NUMERIC", "NOT_NEGATIVE"];

  useEffect(() => { setNType(typeOptions[0]); /* eslint-disable-next-line */ }, [kind]);

  const objTemplates = useMemo(
    () => templates.filter(t => !objectType ||
      (t.name || "").toLowerCase().includes(objectType.split(" ")[0].toLowerCase())),
    [templates, objectType]);

  return (
    <div className="space-y-4">
      <PageTitle
        title="Validation & Cleansing Rules"
        subtitle="Rules scoped by FBDI object + client. Applied at Generate (cleanse + validate the merged output); also runnable on demand."
        right={
          <a className="btn-ghost !h-8" href={DqRulesApi.exportUrl({ target_object: objectType, kind })}>
            <span className="text-xs">Export CSV</span>
          </a>
        }
      />

      <Card>
        <CardBody className="flex flex-wrap items-end gap-3">
          <label className="text-xs">Object
            <select className="input !h-8 !text-xs ml-1" value={objectType} onChange={e => setObjectType(e.target.value)}>
              {objects.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
          <div className="flex rounded-md border border-line overflow-hidden">
            {(["validation", "cleansing"] as const).map(k => (
              <button key={k} onClick={() => setKind(k)}
                className={`px-3 h-8 text-xs ${kind === k ? "bg-brand text-white" : "bg-canvas text-ink-subtle"}`}>
                {k[0].toUpperCase() + k.slice(1)}
              </button>
            ))}
          </div>
          {kind === "validation" && (
            <label className="text-xs">Extract from template
              <select className="input !h-8 !text-xs ml-1" value={templateId} onChange={e => setTemplateId(e.target.value)}>
                <option value="">— template —</option>
                {objTemplates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </label>
          )}
          {kind === "validation" &&
            <Button variant="secondary" className="!h-8" onClick={doExtract}>Extract rules</Button>}
          <Button variant="secondary" className="!h-8" onClick={() => fileRef.current?.click()}>Upload rules</Button>
          <input ref={fileRef} type="file" accept=".csv,.xlsx,.xlsm,.json" className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) doUpload(f); e.currentTarget.value = ""; }} />
          <Button className="!h-8" onClick={() => setAdding(a => !a)}>{adding ? "Cancel" : "Add rule"}</Button>
        </CardBody>
      </Card>

      {adding && (
        <Card>
          <CardHeader title="New rule" />
          <CardBody className="flex flex-wrap items-end gap-3">
            <label className="text-xs">Field (blank = object-wide)
              <input className="input !h-8 !text-xs ml-1" value={nField} onChange={e => setNField(e.target.value)}
                placeholder="e.g. Supplier Number" />
            </label>
            <label className="text-xs">Type
              <select className="input !h-8 !text-xs ml-1" value={nType} onChange={e => setNType(e.target.value)}>
                {typeOptions.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <label className="text-xs">Params (JSON or k=v;k=v)
              <input className="input !h-8 !text-xs ml-1 w-64" value={nParams} onChange={e => setNParams(e.target.value)}
                placeholder='e.g. {"max_length":30} or values=A;values=B' />
            </label>
            {kind === "validation" && (
              <label className="text-xs">Severity
                <select className="input !h-8 !text-xs ml-1" value={nSeverity} onChange={e => setNSeverity(e.target.value)}>
                  <option value="error">error</option><option value="warning">warning</option>
                </select>
              </label>
            )}
            <Button className="!h-8" onClick={addManual}>Save rule</Button>
          </CardBody>
        </Card>
      )}

      {msg && <div className="rounded-md border border-brand/40 bg-brand-subtle/40 px-3 py-2 text-xs text-brand-dark">{msg}</div>}

      <Card>
        <CardHeader title={`${rules.length} ${kind} rule(s) for ${objectType || "—"}`} />
        <CardBody>
          {loading ? <Spinner /> : rules.length === 0 ? (
            <div className="text-xs text-ink-subtle">No rules yet. Extract from a template, upload a file, or add one.</div>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-line text-left text-ink-subtle">
                  <th className="py-1 pr-3">Field</th><th className="py-1 pr-3">Rule</th>
                  <th className="py-1 pr-3">Params</th><th className="py-1 pr-3">Severity</th>
                  <th className="py-1 pr-3">Source</th><th className="py-1 pr-3">Active</th><th></th>
                </tr>
              </thead>
              <tbody>
                {rules.map(r => (
                  <tr key={r.id} className="border-b border-line/60">
                    <td className="py-1 pr-3 font-medium">{r.field || <span className="text-ink-subtle">(object-wide)</span>}</td>
                    <td className="py-1 pr-3 font-mono">{r.rule_type}</td>
                    <td className="py-1 pr-3 font-mono text-[10px] text-ink-subtle">{JSON.stringify(r.params)}</td>
                    <td className="py-1 pr-3">{r.severity}</td>
                    <td className="py-1 pr-3"><Pill tone={r.source === "extracted" ? "info" : r.source === "uploaded" ? "warning" : "neutral"}>{r.source}</Pill></td>
                    <td className="py-1 pr-3">
                      <button onClick={() => toggle(r)} className={`rounded px-2 py-0.5 text-[10px] ${r.active ? "bg-success-subtle text-success-dark" : "bg-canvas text-ink-subtle"}`}>
                        {r.active ? "on" : "off"}
                      </button>
                    </td>
                    <td className="py-1"><button onClick={() => remove(r)} className="text-danger text-[10px]">delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  );
};

export default DataQualityRulesPage;
