import React, { useEffect, useState } from "react";
import {
  Upload, ArrowRight, CheckCircle2, ShieldCheck, AlertTriangle,
  RefreshCw, Database, FileSpreadsheet,
} from "lucide-react";
import { CutoverApi, DatasetsApi } from "@/api";
import { Button, Modal, Pill } from "@/components/ui/Primitives";
import { FileDropzone } from "@/components/ui/FileDropzone";
import { cn } from "@/lib/utils";
import type { Conversion, Environment, EnvironmentRun, Project } from "@/types";

interface Props {
  open: boolean;
  onClose: () => void;
  conversion: Conversion;
  project: Project;
  /** Called after a successful promote so the parent page can refresh. */
  onPromoted: (run: EnvironmentRun) => void;
}

/**
 * Modal for promoting a conversion from one environment to the next
 * (e.g. DEV → QA, QA → UAT, UAT → PROD).
 *
 * The dataflow, mappings, transformations, and validation rules are reused
 * across environments. The user uploads a fresh source extract for the
 * target environment (data shape stays the same; values differ between
 * environments — that's the whole point of multi-env testing).
 */
export const PromoteToEnvironmentModal: React.FC<Props> = ({
  open, onClose, conversion, project, onPromoted,
}) => {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [runs, setRuns] = useState<EnvironmentRun[]>([]);
  const [targetEnvId, setTargetEnvId] = useState<number | null>(null);

  // Source choice: upload new file vs reuse a previous environment's dataset
  const [mode, setMode] = useState<"upload" | "reuse">("upload");
  const [file, setFile] = useState<File | null>(null);
  const [reuseDatasetId, setReuseDatasetId] = useState<number | null>(null);
  const [notes, setNotes] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null); setFile(null); setNotes("");
    CutoverApi.environments(project.id).then(setEnvironments);
    CutoverApi.runsForConversion(conversion.id).then(setRuns);
  }, [open, project.id, conversion.id]);

  // Default the target environment to the next-in-line that hasn't completed
  useEffect(() => {
    if (!environments.length) return;
    if (targetEnvId !== null) return;
    const completed = new Set(
      runs.filter((r) => r.status === "complete").map((r) => r.environment_id)
    );
    const next = environments.find((e) => !completed.has(e.id) && e.name !== "DEV");
    setTargetEnvId(next?.id ?? environments[1]?.id ?? null);
  }, [environments, runs, targetEnvId]);

  const targetEnv = environments.find((e) => e.id === targetEnvId);
  const isProd = targetEnv?.name === "PROD";

  const submit = async () => {
    if (!targetEnvId) return;
    setBusy(true); setError(null);
    try {
      let datasetId: number | null | undefined = null;

      if (mode === "upload") {
        if (!file) {
          setError("Pick a file to upload for this environment.");
          setBusy(false);
          return;
        }
        // 1. Upload the env-specific dataset
        const ds = await DatasetsApi.upload(
          file,
          `${conversion.name} — ${targetEnv?.name ?? "ENV"}`,
          `Source extract uploaded for ${targetEnv?.name ?? ""} environment`
        );
        datasetId = ds.id;
      } else {
        if (!reuseDatasetId) {
          setError("Pick which previous environment's dataset to reuse.");
          setBusy(false);
          return;
        }
        datasetId = reuseDatasetId;
      }

      // 2. Create the EnvironmentRun (this is the promotion)
      const run = await CutoverApi.promote({
        environment_id: targetEnvId,
        conversion_id: conversion.id,
        dataset_id: datasetId,
        notes: notes || undefined,
      });

      onPromoted(run);
      onClose();
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Promotion failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Promote to environment"
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} loading={busy} disabled={!targetEnvId}>
            <ArrowRight className="h-4 w-4" /> Promote {targetEnv?.name && `to ${targetEnv.name}`}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* Reuse explainer */}
        <div className="rounded-md border border-info/30 bg-info-subtle/40 px-3 py-2.5 text-[12.5px]">
          <div className="flex items-start gap-2">
            <RefreshCw className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info" />
            <div>
              <div className="font-semibold text-info">Reusing this conversion's flow</div>
              <div className="mt-0.5 leading-snug text-ink-muted">
                The mappings, transformation rules, validations, and dataflow you built in DEV
                are reused as-is in the target environment. Only the source data changes —
                upload the new environment-specific extract below.
              </div>
            </div>
          </div>
        </div>

        {/* Target environment picker */}
        <div>
          <label className="label">Target environment</label>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {environments.map((e) => {
              const completed = runs.find(
                (r) => r.environment_id === e.id && r.status === "complete"
              );
              const isSelected = targetEnvId === e.id;
              return (
                <button
                  key={e.id}
                  onClick={() => setTargetEnvId(e.id)}
                  disabled={e.name === "DEV"}
                  className={cn(
                    "rounded-md border-2 px-3 py-2 text-left transition",
                    isSelected
                      ? "border-brand bg-brand-subtle/30"
                      : "border-line hover:border-ink-subtle",
                    e.name === "DEV" && "cursor-not-allowed opacity-50",
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-bold tracking-wider text-ink">{e.name}</span>
                    {e.sox_controlled === 1 && (
                      <ShieldCheck className="h-3.5 w-3.5 text-warning" />
                    )}
                  </div>
                  <div className="mt-0.5 text-[10.5px] text-ink-muted">
                    {completed ? (
                      <span className="inline-flex items-center gap-1 text-success">
                        <CheckCircle2 className="h-2.5 w-2.5" /> already loaded
                      </span>
                    ) : e.name === "DEV" ? (
                      "build environment"
                    ) : (
                      "ready"
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* PROD warning */}
        {isProd && (
          <div className="rounded-md border border-danger/30 bg-danger-subtle/50 px-3 py-2.5 text-[12.5px]">
            <div className="flex items-start gap-2">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
              <div>
                <div className="font-semibold text-danger">SOX-controlled environment</div>
                <div className="mt-0.5 leading-snug text-ink-muted">
                  Production load requires dual sign-off by{" "}
                  <code className="rounded bg-white px-1 font-mono">migration_lead</code> and{" "}
                  <code className="rounded bg-white px-1 font-mono">data_owner</code>. This action
                  will create a pending run that must be approved before execution.
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Source choice */}
        <div>
          <label className="label">Source data for this environment</label>
          <div className="mb-2 inline-flex rounded-md border border-line p-0.5">
            <ModeChip active={mode === "upload"} onClick={() => setMode("upload")}>
              <Upload className="h-3 w-3" /> Upload new file
            </ModeChip>
            <ModeChip active={mode === "reuse"} onClick={() => setMode("reuse")}>
              <Database className="h-3 w-3" /> Reuse from previous run
            </ModeChip>
          </div>

          {mode === "upload" ? (
            <FileDropzone
              accept=".csv,.xlsx,.xls"
              helper={`Upload the ${targetEnv?.name ?? "target"} environment's extract — same column shape as the DEV file.`}
              onFile={setFile}
            />
          ) : (
            <div className="space-y-1">
              {runs.filter((r) => r.dataset_id).length === 0 ? (
                <div className="rounded-md border border-line bg-canvas px-3 py-4 text-center text-[12px] text-ink-muted">
                  No previous environment runs to reuse a dataset from yet.
                </div>
              ) : (
                runs.filter((r) => r.dataset_id).map((r) => (
                  <button
                    key={r.id}
                    onClick={() => setReuseDatasetId(r.dataset_id ?? null)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left transition",
                      reuseDatasetId === r.dataset_id
                        ? "border-brand bg-brand-subtle/30"
                        : "border-line hover:border-ink-subtle",
                    )}
                  >
                    <Database className="h-3.5 w-3.5 text-emerald-600" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[12px] font-medium text-ink">
                        {r.dataset_name || `Dataset #${r.dataset_id}`}
                      </div>
                      <div className="text-[10.5px] text-ink-muted">
                        from {r.environment_name} · {r.status}
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        {/* Notes */}
        <div>
          <label className="label">Notes (optional)</label>
          <textarea
            className="input min-h-[60px] !text-xs"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Anything specific about this environment's data — e.g. 'UAT subset filtered to Plano OU only'"
          />
        </div>

        {error && (
          <div className="rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">{error}</div>
        )}
      </div>
    </Modal>
  );
};

const ModeChip: React.FC<{
  active: boolean; onClick: () => void; children: React.ReactNode;
}> = ({ active, onClick, children }) => (
  <button
    onClick={onClick}
    className={cn(
      "inline-flex items-center gap-1 rounded px-2.5 py-1 text-[11.5px] font-medium transition",
      active ? "bg-brand text-white" : "text-ink-muted hover:text-ink",
    )}
  >
    {children}
  </button>
);
