import React, { useEffect, useState } from "react";
import { Building2, Globe, Plus, Star } from "lucide-react";
import { ClientsApi } from "@/api";
import type { ClientSummary } from "@/api";
import {
  Button, Card, CardBody, CardHeader, Modal, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";

/**
 * Clients (tenants) — the top-level grouping for everything the tool knows.
 *
 * Client-scoped knowledge (analyst column mappings, transform rules, gold
 * reference standards) applies ONLY to that client, so a future client never
 * inherits NextPower's source-system assumptions. Oracle-standard knowledge (the
 * FBDI/HDL templates and the public-schema mapping catalog) is GLOBAL and applies
 * to every client — shown here as its own group.
 */
export const ClientsPage: React.FC = () => {
  const [data, setData] = useState<{
    clients: ClientSummary[];
    global: { learnings: number; templates: number };
  } | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = () => ClientsApi.list().then(setData).catch(() => setData({ clients: [], global: { learnings: 0, templates: 0 } }));
  useEffect(() => { void refresh(); }, []);

  const add = async () => {
    if (!name.trim()) return;
    setBusy(true); setErr(null);
    try {
      await ClientsApi.create({ name: name.trim(), code: code.trim() || undefined });
      setAddOpen(false); setName(""); setCode("");
      await refresh();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Could not create client.");
    } finally {
      setBusy(false);
    }
  };

  const makeDefault = async (id: string) => {
    await ClientsApi.update(id, { is_default: true });
    await refresh();
  };

  if (!data) return <PageLoader label="Loading clients…" />;

  return (
    <>
      <PageTitle
        title="Clients"
        subtitle="Every client's mappings, rules and golden records are kept separate — a future client won't inherit another's source-system assumptions. Oracle-standard templates and the public-schema catalog stay global and apply to everyone."
        right={
          <Button onClick={() => setAddOpen(true)}>
            <Plus className="mr-1 h-4 w-4" /> Add client
          </Button>
        }
      />

      {/* Global group */}
      <Card className="mt-5 border-brand/30">
        <CardBody className="flex items-center gap-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-subtle text-brand">
            <Globe className="h-5 w-5" />
          </div>
          <div className="flex-1">
            <div className="text-sm font-semibold text-ink">Global — applies to every client</div>
            <div className="text-[11px] text-ink-muted">
              Oracle-standard FBDI/HDL templates and the public-schema source→FBDI catalog.
            </div>
          </div>
          <div className="flex gap-1.5">
            <Pill tone="brand">{data.global.templates} templates</Pill>
            <Pill tone="info">{data.global.learnings} catalog mappings</Pill>
          </div>
        </CardBody>
      </Card>

      {/* Per-client cards */}
      <Card className="mt-5">
        <CardHeader
          title={<span className="inline-flex items-center gap-1.5"><Building2 className="h-4 w-4 text-brand" /> Clients</span>}
          subtitle="Client-scoped knowledge. Selecting a default is where new projects and un-tagged captures attach."
        />
        <CardBody className="p-0">
          {data.clients.length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-ink-muted">No clients yet.</p>
          ) : (
            data.clients.map(c => (
              <div key={c.id} className="flex items-center gap-3 border-b border-line px-5 py-3 last:border-0">
                <div className="min-w-[200px]">
                  <div className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                    {c.name}
                    {c.is_default && (
                      <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-warning">
                        <Star className="h-3 w-3 fill-current" /> default
                      </span>
                    )}
                  </div>
                  {c.code && <div className="text-[11px] font-mono text-ink-muted">{c.code}</div>}
                </div>
                <div className="flex flex-1 flex-wrap gap-1.5">
                  <Pill tone="brand">{c.counts.learnings} learnings</Pill>
                  <Pill tone="success">{c.counts.gold} gold</Pill>
                  <Pill tone="neutral">{c.counts.projects} projects</Pill>
                  {c.counts.templates > 0 && <Pill tone="info">{c.counts.templates} templates</Pill>}
                </div>
                {!c.is_default && (
                  <Button variant="ghost" onClick={() => void makeDefault(c.id)}>Make default</Button>
                )}
              </div>
            ))
          )}
        </CardBody>
      </Card>

      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        size="md"
        title="Add client"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
            <Button onClick={() => void add()} loading={busy} disabled={!name.trim()}>Create</Button>
          </>
        }
      >
        <div className="space-y-3 text-sm">
          <p className="text-ink-muted">
            A client is a tenant. Its mappings, rules and golden records apply only to its own
            projects; global templates and the public catalog still apply.
          </p>
          <div>
            <div className="mb-1 text-xs font-semibold text-ink">Name</div>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Acme Manufacturing"
              className="w-full rounded-lg border border-line px-3 py-2 text-sm"
            />
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold text-ink">Code <span className="font-normal text-ink-muted">optional</span></div>
            <input
              value={code}
              onChange={e => setCode(e.target.value)}
              placeholder="e.g. ACME"
              className="w-full rounded-lg border border-line px-3 py-2 text-sm"
            />
          </div>
          {err && <div className="rounded-lg border border-danger/30 bg-danger-subtle p-2.5 text-xs text-danger">{err}</div>}
        </div>
      </Modal>
    </>
  );
};

export default ClientsPage;
