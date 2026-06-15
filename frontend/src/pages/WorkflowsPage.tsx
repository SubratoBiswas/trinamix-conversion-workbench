import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Workflow as WfIcon } from "lucide-react";
import { ConversionsApi, WorkflowApi } from "@/api";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";
import { formatDate, statusTone } from "@/lib/utils";
import type {
  Conversion,
  Workflow,
} from "@/types";

export const WorkflowsPage: React.FC = () => {
  const [items, setItems] = useState<Workflow[] | null>(null);
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Conversion[]>([]);
  const [name, setName] = useState("");
  const [pid, setPid] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    WorkflowApi.list().then(setItems);
    ConversionsApi.list().then(setProjects);
  }, []);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const w = await WorkflowApi.create({
        name: name.trim(),
        project_id: pid,
        nodes: [],
        edges: [],
      });
      nav(`/workflows/${w.id}`);
    } finally { setBusy(false); }
  };

  return (
    <>
      <PageTitle
        title="Dataflows"
        subtitle="Visual conversion pipelines built from reusable nodes"
        right={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> New Dataflow</Button>}
      />
      <Card>
        <CardHeader title="All Dataflows" subtitle={`${items?.length ?? 0} dataflow(s)`} />
        {items === null ? <PageLoader /> :
          items.length === 0 ? <CardBody><EmptyState
            icon={<WfIcon className="h-5 w-5" />}
            title="No dataflows yet"
            description="Build a visual pipeline that ties dataset → mapping → transformation → validation → load."
            action={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Create Dataflow</Button>}
          /></CardBody> : (
          <table className="table-shell">
            <thead>
              <tr><th>Name</th><th>Project</th><th>Status</th><th>Last Run</th><th>Updated</th><th></th></tr>
            </thead>
            <tbody>
              {items.map(w => (
                <tr key={w.id}>
                  <td className="font-medium">{w.name}</td>
                  <td className="text-ink-muted">{projects.find(p => p.id === w.conversion_id)?.name || "—"}</td>
                  <td><Pill tone={statusTone(w.status)}>{w.status}</Pill></td>
                  <td className="text-ink-muted">{formatDate(w.last_run_at)}</td>
                  <td className="text-ink-muted">{formatDate(w.updated_at)}</td>
                  <td className="text-right"><Link to={`/workflows/${w.id}`} className="btn-ghost h-7 px-2 text-xs">Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="New Dataflow" footer={<>
        <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
        <Button onClick={create} loading={busy}>Create</Button>
      </>}>
        <div className="space-y-4">
          <div><label className="label">Name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Item Master Dataflow" /></div>
          <div>
            <label className="label">Bind to project (optional)</label>
            <select className="input" value={pid ?? ""} onChange={(e) => setPid(e.target.value ? Number(e.target.value) : null)}>
              <option value="">— none —</option>
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        </div>
      </Modal>
    </>
  );
};
