import React, { useRef, useState } from "react";
import { Sparkles, Upload, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { ConversionsApi } from "@/api";

type LearnResult = Awaited<ReturnType<typeof ConversionsApi.learnFromExample>>;

/**
 * "Learn from example / steer" panel — lets the user teach a conversion by
 * uploading a populated example output (AI infers source->target mappings +
 * constant defaults) and/or by typing plain-text steering instructions
 * (e.g. "default Business Relationship to PROSPECTIVE"). Applies the results as
 * approved mappings, then calls onApplied() so the parent reloads.
 */
export default function LearnFromExamplePanel({
  conversionId,
  onApplied,
}: {
  conversionId: string;
  onApplied?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [prompt, setPrompt] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<LearnResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const run = async () => {
    if (!file && !prompt.trim()) {
      setError("Add an example file or a prompt (or both).");
      return;
    }
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await ConversionsApi.learnFromExample(conversionId, {
        file: file ?? undefined,
        prompt: prompt.trim() || undefined,
      });
      setResult(res);
      onApplied?.();
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Learning failed.");
    } finally {
      setRunning(false);
    }
  };

  const learned = result?.learned;
  const steer = result?.steer;

  return (
    <div className="border-b border-line bg-gradient-to-r from-amber-50 to-white px-5 py-2.5 text-[12px]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 text-left"
        title="Teach the tool from a filled-in example, or steer it with a prompt"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 text-amber-600" /> : <ChevronRight className="h-3.5 w-3.5 text-amber-600" />}
        <Sparkles className="h-4 w-4 text-amber-600" />
        <span className="font-semibold text-ink">Learn from example &amp; steer</span>
        <span className="text-ink-muted">— upload a gold output and/or type instructions to fix the mapping</span>
      </button>

      {open && (
        <div className="mt-2.5 space-y-2.5 pl-6">
          {/* Example upload */}
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,.xlsm,.xls,.csv"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <button
              onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white px-2.5 py-1.5 text-[11px] font-medium text-ink hover:border-amber-400"
            >
              <Upload className="h-3.5 w-3.5" /> Choose example output…
            </button>
            {file && (
              <span className="font-mono text-[11px] text-ink-muted">
                {file.name}
                <button className="ml-2 text-ink-subtle hover:text-red-500" onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = ""; }}>✕</button>
              </span>
            )}
          </div>

          {/* Prompt steering */}
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={2}
            placeholder={"Steering instructions (one per line), e.g.\ndefault Business Relationship to PROSPECTIVE\nmap Supplier Name from Name"}
            className="w-full resize-y rounded-md border border-line bg-white px-2.5 py-1.5 font-mono text-[11px] text-ink placeholder:text-ink-subtle focus:border-amber-400 focus:outline-none"
          />

          <div className="flex items-center gap-2">
            <button
              onClick={run}
              disabled={running}
              className="inline-flex items-center gap-1.5 rounded-md bg-amber-500 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-amber-600 disabled:opacity-60"
            >
              {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              {running ? "Learning…" : "Learn & apply"}
            </button>
            {error && <span className="text-[11px] text-red-600">{error}</span>}
          </div>

          {/* Results */}
          {result && (
            <div className="rounded-md border border-line bg-white px-3 py-2 text-[11px]">
              {learned && (
                <div className="mb-1.5">
                  <span className="font-semibold text-ink">From example:</span>{" "}
                  <span className="text-emerald-700">{learned.mapped_count} mapped</span>,{" "}
                  <span className="text-brand-dark">{learned.default_count} defaults</span>,{" "}
                  {typeof learned.suppressed_count === "number" && learned.suppressed_count > 0 && (
                    <><span className="text-amber-700">{learned.suppressed_count} kept blank (per gold)</span>,{" "}</>
                  )}
                  <span className="text-ink-muted">{learned.skipped} left for review</span>
                  {learned.mapped.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {learned.mapped.map((m, i) => (
                        <span key={i} className="inline-flex items-center gap-1 rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 font-mono text-[10px] text-emerald-800">
                          {m.field} ← {m.source} <span className="text-emerald-500">{Math.round(m.match * 100)}%</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {steer && (steer.applied.length > 0 || steer.unmatched.length > 0) && (
                <div>
                  <span className="font-semibold text-ink">From prompt:</span>{" "}
                  <span className="text-emerald-700">{steer.applied.length} applied</span>
                  {steer.unmatched.length > 0 && (
                    <span className="text-ink-muted"> · couldn't parse: {steer.unmatched.join("; ")}</span>
                  )}
                </div>
              )}
              <div className="mt-1.5 text-[10px] text-ink-muted">Run <span className="font-semibold">Generate Output</span> to produce the updated file.</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
