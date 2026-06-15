import React, { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Plus, Trash2, Wand2 } from "lucide-react";
import { ConversionsApi, DatasetsApi, FbdiApi, MappingApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, PageLoader,
  PageTitle, Pill,
} from "@/components/ui/Primitives";
import { RuleAuthorModal } from "@/components/transforms/RuleAuthorModal";
import type {
  Conversion,
  DatasetDetail,
  FBDIField,
  TransformationRule,
} from "@/types";

export const TransformationStudioPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const projParam = params.get("conversion");
  const [projects, setProjects] = useState<Conversion[]>([]);
  const [pid, setPid] = useState<number | null>(projParam ? Number(projParam) : null);
  const [fields, setFields] = useState<FBDIField[]>([]);
  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  const [rules, setRules] = useState<TransformationRule[] | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    ConversionsApi.list().then((ps) => {
      setProjects(ps);
      if (!pid && ps[0]) {
        setPid(ps[0].id);
        setParams({ conversion: String(ps[0].id) });
      }
    });
  }, []);

  const refresh = async () => {
    if (!pid) return;
    setRules(null);
    const proj = await ConversionsApi.get(pid);
    if (!proj.template_id) {
      setFields([]); setRules([]); setDataset(null);
      return;
    }
    const [fs, rs, ds] = await Promise.all([
      FbdiApi.fields(proj.template_id),
      MappingApi.rules(pid),
      proj.dataset_id ? DatasetsApi.get(proj.dataset_id) : Promise.resolve(null as any),
    ]);
    setFields(fs); setRules(rs); setDataset(ds);
  };
  useEffect(() => { refresh(); }, [pid]);

  return (
    <>
      <PageTitle
        title="Transformation Studio"
        subtitle="Build rules that convert source values into Fusion-ready format"
        right={<Button onClick={() => setOpen(true)} disabled={!pid}><Plus className="h-4 w-4" /> Add Rule</Button>}
      />

      <Card className="mb-4">
        <CardBody className="!py-3">
          <div className="flex items-center gap-3">
            <label className="label !mb-0">Project</label>
            <select className="input !w-auto min-w-[280px]" value={pid ?? ""}
              onChange={(e) => { const v = Number(e.target.value); setPid(v); setParams({ conversion: String(v) }); }}>
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Active Rules" subtitle={`${rules?.length ?? 0} rule(s)`} />
        {rules === null ? <PageLoader /> :
          rules.length === 0 ? <CardBody><EmptyState
            icon={<Wand2 className="h-5 w-5" />}
            title="No transformation rules yet"
            description="Add rules to clean, format, or remap source values before they hit FBDI columns."
            action={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Add Rule</Button>}
          /></CardBody> : (
          <table className="table-shell">
            <thead>
              <tr>
                <th>#</th><th>Rule</th><th>Target Field</th><th>Source Column</th>
                <th>Config</th><th>Description</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r, idx) => {
                const tf = fields.find(f => f.id === r.target_field_id);
                return (
                  <tr key={r.id}>
                    <td className="text-ink-muted">{idx + 1}</td>
                    <td><Pill tone="brand">{r.rule_type}</Pill></td>
                    <td className="font-medium">{tf?.field_name || "—"}</td>
                    <td>{r.source_column ? <code className="rounded bg-canvas px-1.5 py-0.5 text-[12px]">{r.source_column}</code> : "—"}</td>
                    <td className="max-w-[260px] truncate font-mono text-[11px] text-ink-muted">{JSON.stringify(r.rule_config) || "{}"}</td>
                    <td className="text-ink-muted">{r.description || "—"}</td>
                    <td className="text-right">
                      <button onClick={async () => { await MappingApi.deleteRule(r.id); refresh(); }} className="btn-ghost h-7 px-2 text-xs text-danger hover:bg-danger-subtle">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      {pid && dataset && (
        <RuleAuthorModal
          open={open}
          onClose={() => setOpen(false)}
          conversionId={pid}
          fields={fields}
          sourceColumns={dataset.columns}
          onSaved={() => { setOpen(false); refresh(); }}
        />
      )}
    </>
  );
};
