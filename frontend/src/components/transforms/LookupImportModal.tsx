import React, { useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, Download, Upload } from "lucide-react";
import { FbdiApi, type LookupImportResult, type LookupStatus } from "@/api";
import { Button, Modal, Pill, Spinner } from "@/components/ui/Primitives";

/**
 * Import the customer's own Fusion lookup codes.
 *
 * The FBDI templates name lookup types (EGP_MATERIAL_PLANNING, EGP_SOURCE_TYPES…)
 * without publishing their codes — those are configured per instance. Until they're
 * imported the tool passes those columns through untouched rather than guessing.
 * This is how you close that gap for good: one export from Manage Standard Lookups,
 * and every column using those types becomes fully mapped and validated.
 */
const LookupImportModal: React.FC<{
  open: boolean;
  onClose: () => void;
  onImported?: () => void;
}> = ({ open, onClose, onImported }) => {
  const [status, setStatus] = useState<LookupStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LookupImportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadStatus = async () => {
    setLoading(true);
    try {
      setStatus(await FbdiApi.lookupStatus());
    } catch {
      /* non-fatal — the import still works */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      setResult(null);
      setErr(null);
      void loadStatus();
    }
  }, [open]);

  const onPick = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await FbdiApi.importLookups(f);
      setResult(r);
      await loadStatus();
      onImported?.();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Import failed. Check the file has a lookup type and lookup code column.");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const s = status?.summary;

  return (
    <Modal open={open} onClose={onClose} title="Import Oracle lookup codes">
      <div className="space-y-4 text-sm">
        <p className="text-ink-muted">
          Some FBDI columns get their accepted values from a lookup type in{" "}
          <span className="font-medium text-ink">your</span> Fusion instance — the codes
          aren't published in the template, so the tool won't invent them. Export them once
          and every column that uses them becomes fully mapped.
        </p>

        <div className="rounded-lg border border-line bg-canvas p-3 text-xs text-ink-muted">
          <div className="mb-1 font-semibold text-ink">Where to get the file</div>
          In Fusion: <span className="font-medium">Setup and Maintenance → Manage Standard
          Lookups</span> → search the lookup type → export to Excel. Any CSV or XLSX with a
          lookup type column and a lookup code column will work.
        </div>

        {loading && !status ? (
          <div className="flex items-center gap-2 text-ink-muted">
            <Spinner /> Checking which lookups your templates need…
          </div>
        ) : s ? (
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone="neutral">{s.referenced} lookup types needed</Pill>
            {s.imported > 0 && <Pill tone="success">{s.imported} imported</Pill>}
            {s.missing > 0 && <Pill tone="warning">{s.missing} still missing</Pill>}
            {s.total_codes > 0 && <Pill tone="info">{s.total_codes} codes on file</Pill>}
          </div>
        ) : null}

        {status && status.lookup_types.length > 0 && (
          <div className="max-h-56 overflow-auto rounded-lg border border-line">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-canvas text-ink-muted">
                <tr>
                  <th className="px-3 py-1.5 text-left font-medium">Lookup type</th>
                  <th className="px-3 py-1.5 text-right font-medium">Columns</th>
                  <th className="px-3 py-1.5 text-right font-medium">Codes</th>
                  <th className="px-3 py-1.5 text-left font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {status.lookup_types.map(l => (
                  <tr key={l.lookup_type} className="border-t border-line">
                    <td className="px-3 py-1.5 font-mono text-[11px] text-ink">{l.lookup_type}</td>
                    <td className="px-3 py-1.5 text-right text-ink-muted">{l.columns_using_it}</td>
                    <td className="px-3 py-1.5 text-right text-ink-muted">{l.codes || "—"}</td>
                    <td className="px-3 py-1.5">
                      {l.status === "imported" ? (
                        <span className="inline-flex items-center gap-1 text-success">
                          <CheckCircle2 className="h-3.5 w-3.5" /> imported
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-warning">
                          <CircleAlert className="h-3.5 w-3.5" /> not imported
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {result && (
          <div className="rounded-lg border border-success/30 bg-success-subtle p-3 text-xs text-ink">
            <div className="font-semibold text-success">
              Imported {result.codes_imported} codes across {result.lookup_types.length} lookup types.
            </div>
            <div className="mt-1 text-ink-muted">
              {result.fields_updated} template columns now validate against your instance's codes.
              {result.types_not_used_by_any_template.length > 0 && (
                <> {result.types_not_used_by_any_template.length} imported types aren't used by any
                loaded template — harmless, they'll apply if you load a template that needs them.</>
              )}
              {result.types_still_missing.length > 0 && (
                <> Still missing codes for {result.types_still_missing.length} types.</>
              )}
            </div>
          </div>
        )}

        {err && (
          <div className="rounded-lg border border-danger/30 bg-danger-subtle p-3 text-xs text-danger">
            {err}
          </div>
        )}

        <input
          ref={fileRef}
          type="file"
          accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls"
          className="hidden"
          onChange={e => void onPick(e.target.files?.[0])}
        />

        <div className="flex items-center justify-between pt-1">
          <a
            className="inline-flex items-center gap-1 text-xs text-brand hover:underline"
            href={
              "data:text/csv;charset=utf-8," +
              encodeURIComponent(
                "Lookup Type,Lookup Code,Meaning,Enabled\nEGP_MATERIAL_PLANNING,1,Not planned,Y\nEGP_MATERIAL_PLANNING,3,Min-max planning,Y\n"
              )
            }
            download="lookup_codes_template.csv"
          >
            <Download className="h-3.5 w-3.5" /> Download a blank format
          </a>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>Close</Button>
            <Button onClick={() => fileRef.current?.click()} loading={busy}>
              <Upload className="mr-1 h-4 w-4" /> Choose file
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
};

export default LookupImportModal;
