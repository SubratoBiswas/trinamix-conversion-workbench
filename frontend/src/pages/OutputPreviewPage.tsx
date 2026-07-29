import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, Copy, Download, FileOutput, FolderDown, RotateCcw, Sparkles, Wand2,
} from "lucide-react";
import { ConversionsApi, OutputApi, QualityApi } from "@/api";
import {
  Button, Card, CardBody, EmptyState, Modal, PageLoader, PageTitle, Pill, Tabs,
} from "@/components/ui/Primitives";
import { cn, confidenceTone, severityTone } from "@/lib/utils";
import type {
  CleansingFamily,
  CleansingFinding,
  CleansingPreview,
  CleansingProfile,
  CleansingProfileInfo,
  CleansingVerdict,
  Conversion,
  DecisionInput,
  DuplicateCandidates,
  DuplicateCluster,
  DuplicateDecision,
  DuplicateVerdict,
  OutputPreview,
  ReviewBundle,
} from "@/types";

// ─── Decision vocabulary (mirrors RowDecision.DUP_VERDICTS on the backend) ───
const DUP_LABEL: Record<DuplicateVerdict, string> = {
  merge: "Merged into a golden record",
  keep_survivor: "Keeping the nominated survivor only",
  keep_all: "Keeping all — genuinely different",
  exclude: "Excluded from the output",
  keep_subset: "Keeping the selected records",
};
const DUP_TONE: Record<DuplicateVerdict, "brand" | "success" | "danger" | "info"> = {
  merge: "brand",
  keep_survivor: "brand",
  keep_all: "success",
  exclude: "danger",
  keep_subset: "brand",
};
const NEEDS_SURVIVOR: DuplicateVerdict[] = ["merge", "keep_survivor"];
const HIGH_CONFIDENCE = 0.95;
const SEVERITY_ORDER = ["critical", "error", "warning", "info"];

/** A cleansing finding key can repeat across issue rows — collapse to one card. */
interface GroupedFinding extends CleansingFinding {
  occurrences: number;
}

const errText = (e: unknown): string => {
  const err = e as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  return err?.message || "Something went wrong. Please try again.";
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Recompute the decided/undecided counters the generation gate reads. */
const withCounts = (d: DuplicateCandidates, clusters: DuplicateCluster[]): DuplicateCandidates => {
  const decided = clusters.filter(c => c.decision).length;
  return { ...d, clusters, decided_count: decided, undecided_count: clusters.length - decided };
};

interface ConfirmState {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  tone: "primary" | "danger";
  onConfirm: () => void;
}

export const OutputPreviewPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const pid = id;
  const [project, setProject] = useState<Conversion | null>(null);
  const [data, setData] = useState<OutputPreview | null>(null);
  const [tab, setTab] = useState("data");
  const [generating, setGenerating] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadingAll, setDownloadingAll] = useState(false);
  // Fuzzy duplicate / entity resolution (loaded on demand when the tab opens).
  const [dupes, setDupes] = useState<DuplicateCandidates | null>(null);
  const [dupLoading, setDupLoading] = useState(false);
  const [dupAi, setDupAi] = useState(false);
  const [dupError, setDupError] = useState<string | null>(null);
  // Cleansing findings + the counters the generation gate warns on (cheap, load on mount).
  const [review, setReview] = useState<ReviewBundle | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  // Per-cluster / per-finding UI state.
  const [survivors, setSurvivors] = useState<Record<string, string>>({});
  // Pending "keep these rows" ticks, per cluster. Absent = the user has not
  // touched the checkboxes, so the saved verdict (or none) still describes the
  // cluster; present = they are mid-edit and it overrides the saved one.
  const [subsetKeeps, setSubsetKeeps] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [rowError, setRowError] = useState<Record<string, string>>({});
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  // A saved decision changes what generation would produce — the file on disk is stale.
  const [decisionsDirty, setDecisionsDirty] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  // Cleansing rule families: which run at generation, and a dry-run of what they
  // would change. Kept separate from the findings list because these are STANDING
  // rules, not one-off verdicts on a specific flagged value.
  const [profile, setProfile] = useState<CleansingProfileInfo | null>(null);
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [cleansePreview, setCleansePreview] = useState<CleansingPreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  // In-flight edits to a preview row's "After" value, keyed field|rule. Held
  // separately from the profile so typing does not fire a save per keystroke.
  const [edits, setEdits] = useState<Record<string, string>>({});

  const setBusyKey = (key: string, on: boolean) =>
    setBusy(prev => ({ ...prev, [key]: on }));
  const setErrorKey = (key: string, msg: string | null) =>
    setRowError(prev => {
      const next = { ...prev };
      if (msg) next[key] = msg; else delete next[key];
      return next;
    });

  const loadDupes = async (useAi = false) => {
    if (!pid) return;
    setDupLoading(true);
    setDupError(null);
    try {
      setDupes(await OutputApi.duplicateCandidates(pid, { useAi }));
    } catch (e) {
      setDupes(null);
      setDupError(
        `Couldn't analyze duplicates — generate/preview the output first. (${errText(e)})`,
      );
    } finally { setDupLoading(false); }
  };

  const loadReview = async (silent = false) => {
    if (!pid) return;
    if (!silent) setReviewLoading(true);
    try {
      setReview(await OutputApi.reviewBundle(pid));
      setReviewError(null);
    } catch (e) {
      if (!silent) { setReview(null); setReviewError(errText(e)); }
    } finally { if (!silent) setReviewLoading(false); }
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await OutputApi.download(pid!, `${project?.template_name ?? "output"}.csv`);
    } catch {
      alert("No output file found — please generate output first.");
    } finally {
      setDownloading(false);
    }
  };

  // Download every relevant FBDI file for this engagement in one zip, generated
  // fresh from the current mappings and named/ordered by the load sequence.
  const handleDownloadAll = async () => {
    if (!project?.project_id) return;
    setDownloadingAll(true);
    try {
      await OutputApi.downloadAll(
        String(project.project_id),
        `${(project.project_name ?? "engagement").replace(/[^\w.-]+/g, "_")}_FBDI.zip`,
      );
    } catch (e: any) {
      alert(
        e?.response?.status === 400
          ? "No conversions are ready to generate FBDI output yet (each needs a source file and a bound template)."
          : "Could not build the FBDI bundle. Please try again.",
      );
    } finally {
      setDownloadingAll(false);
    }
  };

  const refresh = async () => {
    setData(null);
    OutputApi.preview(pid!, 50).then(setData).catch(() => setData(null));
  };

  useEffect(() => {
    if (!pid) return;
    ConversionsApi.get(pid).then(setProject);
    refresh();
    loadReview();
    void loadProfile();
  }, [pid]);

  const runGenerate = async () => {
    setGenerating(true);
    try {
      // Async: kick off + poll until the artifact is ready (nothing blocks the
      // request thread, so heavy multi-sheet objects can't hit the gateway timeout).
      await OutputApi.generateAndWait(pid!, "csv");
      setDecisionsDirty(false);
      await refresh();
      await loadReview(true);
    } catch (e: any) {
      alert(e?.message || "Couldn't generate the output. Please try again.");
    } finally { setGenerating(false); }
  };

  // ─── Generation gate: warn (never block) when adjudication is still open ───
  const undecidedDupes = dupes?.undecided_count ?? 0;
  const openCleansing = review?.cleansing_open ?? 0;

  const generate = () => {
    if (undecidedDupes === 0 && openCleansing === 0) { void runGenerate(); return; }
    const parts: string[] = [];
    if (undecidedDupes > 0) parts.push(plural(undecidedDupes, "duplicate cluster"));
    if (openCleansing > 0) parts.push(plural(openCleansing, "cleansing finding"));
    const subject = parts.join(" and ");
    const verb = (undecidedDupes > 0 && openCleansing > 0) || undecidedDupes > 1 || openCleansing > 1
      ? "are" : "is";
    setConfirm({
      title: "Undecided review items",
      confirmLabel: "Generate anyway",
      tone: "primary",
      body: (
        <div className="space-y-2 text-sm text-ink-muted">
          <p className="text-ink">{subject} {verb} undecided — generate anyway?</p>
          <p>
            Undecided duplicates are written out as-is (no merge, no exclusion) and undecided
            cleansing fixes are not applied. You can decide them on the{" "}
            <span className="font-medium">Duplicate suspects</span> and{" "}
            <span className="font-medium">Cleansing</span> tabs and re-generate at any time.
          </p>
        </div>
      ),
      onConfirm: () => { setConfirm(null); void runGenerate(); },
    });
  };

  // ─── Duplicate decisions (optimistic, reverted on error) ───
  const patchCluster = (clusterKey: string, decision: DuplicateDecision | null) =>
    setDupes(prev => prev
      ? withCounts(prev, prev.clusters.map(c => c.cluster_key === clusterKey ? { ...c, decision } : c))
      : prev);

  const decideCluster = async (cl: DuplicateCluster, verdict: DuplicateVerdict,
                               keepKeys?: string[]) => {
    if (!pid) return;
    const survivor = survivors[cl.cluster_key] ?? cl.decision?.survivor_key ?? null;
    if (NEEDS_SURVIVOR.includes(verdict) && !survivor) {
      setErrorKey(cl.cluster_key, "Nominate which record survives first.");
      return;
    }
    if (verdict === "keep_subset" && !keepKeys?.length) {
      setErrorKey(cl.cluster_key, "Tick at least one record to keep.");
      return;
    }
    const survivorKey = NEEDS_SURVIVOR.includes(verdict) ? survivor : null;
    const keep = verdict === "keep_subset" ? keepKeys ?? [] : [];
    const previous = cl.decision;
    // Any other verdict supersedes a half-edited tick-list; leaving it pending
    // would keep overriding the verdict just saved.
    if (verdict !== "keep_subset") {
      setSubsetKeeps(prev => {
        const next = { ...prev };
        delete next[cl.cluster_key];
        return next;
      });
    }
    setErrorKey(cl.cluster_key, null);
    setBusyKey(cl.cluster_key, true);
    patchCluster(cl.cluster_key, { verdict, survivor_key: survivorKey,
                                   keep_keys: keep, source: "conversion" });
    try {
      await OutputApi.saveDecisions(pid, [{
        scope: "duplicate",
        decision_key: cl.cluster_key,
        verdict,
        survivor_key: survivorKey,
        keep_keys: keep,
        member_keys: cl.member_keys,
        label: cl.fields.join(", "),
      }]);
      setDecisionsDirty(true);
      void loadReview(true);
    } catch (e) {
      patchCluster(cl.cluster_key, previous);
      setErrorKey(cl.cluster_key, errText(e));
    } finally { setBusyKey(cl.cluster_key, false); }
  };

  const undoCluster = async (cl: DuplicateCluster) => {
    if (!pid) return;
    const previous = cl.decision;
    setErrorKey(cl.cluster_key, null);
    setBusyKey(cl.cluster_key, true);
    // Undo has to clear the pending ticks too, or the cluster would still render
    // as a subset even though the saved decision is gone.
    setSubsetKeeps(prev => {
      const next = { ...prev };
      delete next[cl.cluster_key];
      return next;
    });
    patchCluster(cl.cluster_key, null);
    try {
      await OutputApi.clearDecisions(pid, cl.cluster_key);
      setDecisionsDirty(true);
      void loadReview(true);
    } catch (e) {
      patchCluster(cl.cluster_key, previous);
      setErrorKey(cl.cluster_key, errText(e));
    } finally { setBusyKey(cl.cluster_key, false); }
  };

  const highConfidenceTargets = useMemo(
    () => (dupes?.clusters ?? []).filter(c => c.confidence >= HIGH_CONFIDENCE && !c.decision),
    [dupes],
  );

  const mergeHighConfidence = async (targets: DuplicateCluster[]) => {
    if (!pid || targets.length === 0) return;
    const snapshot = new Map(targets.map(c => [c.cluster_key, c.decision] as const));
    setBulkError(null);
    setBulkBusy(true);
    const payload: DecisionInput[] = targets.map(c => ({
      scope: "duplicate",
      decision_key: c.cluster_key,
      verdict: "merge",
      survivor_key: survivors[c.cluster_key] ?? null,
      member_keys: c.member_keys,
      label: c.fields.join(", "),
    }));
    setDupes(prev => prev ? withCounts(prev, prev.clusters.map(c => snapshot.has(c.cluster_key)
      ? { ...c, decision: { verdict: "merge", survivor_key: survivors[c.cluster_key] ?? null, source: "conversion" } }
      : c)) : prev);
    try {
      await OutputApi.saveDecisions(pid, payload);
      setDecisionsDirty(true);
      void loadReview(true);
    } catch (e) {
      setDupes(prev => prev ? withCounts(prev, prev.clusters.map(c => snapshot.has(c.cluster_key)
        ? { ...c, decision: snapshot.get(c.cluster_key) ?? null } : c)) : prev);
      setBulkError(errText(e));
    } finally { setBulkBusy(false); }
  };

  const clearAllDecisions = async () => {
    if (!pid) return;
    const snapshot = dupes?.clusters ?? [];
    setBulkError(null);
    setBulkBusy(true);
    // Verdicts learned from an earlier conversion are not this conversion's to clear.
    setDupes(prev => prev ? withCounts(prev, prev.clusters.map(c =>
      c.decision?.source === "conversion" ? { ...c, decision: null } : c)) : prev);
    try {
      await OutputApi.clearDecisions(pid);
      setDecisionsDirty(true);
      await loadReview(true);
    } catch (e) {
      setDupes(prev => prev ? withCounts(prev, prev.clusters.map(c => {
        const before = snapshot.find(s => s.cluster_key === c.cluster_key);
        return before ? { ...c, decision: before.decision } : c;
      })) : prev);
      setBulkError(errText(e));
    } finally { setBulkBusy(false); }
  };

  // ─── Cleansing rule families ───
  const loadProfile = async () => {
    if (!pid) return;
    setProfileError(null);
    try {
      setProfile(await OutputApi.cleansingProfile(pid));
    } catch (e) { setProfileError(errText(e)); }
  };

  const toggleFamily = async (key: CleansingFamily) => {
    if (!pid || !profile) return;
    const on = profile.profile.families.includes(key);
    const families = on
      ? profile.profile.families.filter(f => f !== key)
      : [...profile.profile.families, key];
    const next = { ...profile.profile, families };
    setProfile({ ...profile, profile: next, is_default: false,
                 families: profile.families.map(f =>
                   f.key === key ? { ...f, enabled: !on } : f) });
    setProfileBusy(true);
    setProfileError(null);
    try {
      await OutputApi.saveCleansingProfile(pid, next);
      // The rules change what generation writes, exactly like a duplicate verdict,
      // so the same "re-generate" banner applies.
      setDecisionsDirty(true);
      setCleansePreview(null);
    } catch (e) {
      setProfileError(errText(e));
      await loadProfile();                       // roll back to the server's truth
    } finally { setProfileBusy(false); }
  };

  const runCleansePreview = async (families?: CleansingFamily[]) => {
    if (!pid) return;
    setPreviewBusy(true);
    setProfileError(null);
    try {
      setCleansePreview(await OutputApi.cleansingPreview(pid, families));
    } catch (e) { setProfileError(errText(e)); }
    finally { setPreviewBusy(false); }
  };

  /** Persist a profile edit, then re-run the preview so the table reflects it. */
  const saveProfile = async (next: CleansingProfile) => {
    if (!pid || !profile) return;
    const before = profile;
    setProfile({ ...profile, profile: next, is_default: false });
    setProfileBusy(true);
    setProfileError(null);
    try {
      await OutputApi.saveCleansingProfile(pid, next);
      setDecisionsDirty(true);
      await runCleansePreview(next.families);
    } catch (e) {
      setProfile(before);
      setProfileError(errText(e));
    } finally { setProfileBusy(false); }
  };

  /** Stop one family touching one field, without disabling it everywhere else. */
  const skipRuleForField = (field: string, rule: CleansingFamily) => {
    if (!profile) return;
    const p = profile.profile;
    const current = p.per_field?.[field] ?? p.families;
    void saveProfile({
      ...p,
      per_field: { ...(p.per_field ?? {}), [field]: current.filter(f => f !== rule) },
    });
  };

  /** Pin a replacement for one specific value. Beats every rule. */
  const overrideValue = (field: string, before: string, after: string) => {
    if (!profile) return;
    const p = profile.profile;
    void saveProfile({
      ...p,
      value_overrides: {
        ...(p.value_overrides ?? {}),
        [field]: { ...((p.value_overrides ?? {})[field] ?? {}), [before]: after },
      },
    });
  };

  const clearOverride = (field: string, before: string) => {
    if (!profile) return;
    const p = profile.profile;
    const forField = { ...((p.value_overrides ?? {})[field] ?? {}) };
    delete forField[before];
    const next = { ...(p.value_overrides ?? {}) };
    if (Object.keys(forField).length) next[field] = forField; else delete next[field];
    void saveProfile({ ...p, value_overrides: next });
  };

  /** Profile the source dataset for data-quality findings.
   *  Lives on the Data Quality page and Conversion detail, but the findings are
   *  REVIEWED here — so without this the list can only ever be empty for anyone
   *  who starts on Output Preview. */
  const runProfileCleansing = async () => {
    if (!pid) return;
    setProfileBusy(true);
    setProfileError(null);
    try {
      await QualityApi.runCleansing(pid);
      await loadReview(true);
    } catch (e) { setProfileError(errText(e)); }
    finally { setProfileBusy(false); }
  };

  // ─── Cleansing decisions ───
  const patchFinding = (key: string, verdict: CleansingVerdict | null) =>
    setReview(prev => {
      if (!prev) return prev;
      const cleansing = prev.cleansing.map(f => f.key === key ? { ...f, verdict } : f);
      return { ...prev, cleansing, cleansing_open: cleansing.filter(f => !f.verdict).length };
    });

  const decideFinding = async (f: GroupedFinding, verdict: CleansingVerdict | null) => {
    if (!pid) return;
    const previous = f.verdict;
    setErrorKey(f.key, null);
    setBusyKey(f.key, true);
    patchFinding(f.key, verdict);
    try {
      if (verdict === null) {
        await OutputApi.clearDecisions(pid, f.key);
      } else {
        await OutputApi.saveDecisions(pid, [{
          scope: "cleansing", decision_key: f.key, verdict,
          label: f.field_name ?? f.issue_type ?? undefined,
        }]);
      }
      setDecisionsDirty(true);
    } catch (e) {
      patchFinding(f.key, previous);
      setErrorKey(f.key, errText(e));
    } finally { setBusyKey(f.key, false); }
  };

  /** Identity column names — identity_columns is always plain strings; identity_fields
   *  can arrive as objects on the scanner's early-return branches, so filter it. */
  const idCols: string[] = useMemo(() => {
    const cols = dupes?.identity_columns?.length ? dupes.identity_columns : (dupes?.identity_fields ?? []);
    return cols.filter((c): c is string => typeof c === "string");
  }, [dupes]);

  const cleansingGroups = useMemo(() => {
    const byKey = new Map<string, GroupedFinding>();
    for (const f of review?.cleansing ?? []) {
      const seen = byKey.get(f.key);
      if (seen) {
        seen.occurrences += 1;
        seen.impacted_count += f.impacted_count ?? 0;
      } else {
        byKey.set(f.key, { ...f, occurrences: 1 });
      }
    }
    const all = [...byKey.values()];
    const buckets = SEVERITY_ORDER
      .map(sev => ({ severity: sev, items: all.filter(f => (f.severity || "").toLowerCase() === sev) }))
      .filter(b => b.items.length > 0);
    const other = all.filter(f => !SEVERITY_ORDER.includes((f.severity || "").toLowerCase()));
    if (other.length) buckets.push({ severity: "other", items: other });
    return buckets;
  }, [review]);

  const cleansingCount = useMemo(
    () => cleansingGroups.reduce((n, b) => n + b.items.length, 0), [cleansingGroups]);

  if (!project) return <PageLoader />;

  // ─── Tab renderers ───
  const renderData = (d: OutputPreview) => (
    d.columns.length === 0 ? (
      <CardBody><EmptyState
        title="No converted output yet"
        description="Approve at least one mapping then click Re-generate."
      /></CardBody>
    ) : (
      <div className="overflow-x-auto">
        <table className="table-shell">
          <thead>
            <tr>
              <th>#</th>
              {d.columns.map(c => <th key={c} className="whitespace-nowrap">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {d.rows.map((row, i) => (
              <tr key={i}>
                <td className="text-ink-muted">{i + 1}</td>
                {d.columns.map(col => (
                  <td key={col} className="whitespace-nowrap text-ink-muted">{String(row[col] ?? "")}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  );

  const renderLineage = (d: OutputPreview) => (
    <table className="table-shell">
      <thead>
        <tr>
          <th>Target Field</th><th>Source Column</th>
          <th>Default</th><th>Rules Applied</th>
          <th>Confidence</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(d.lineage).map(([target, lin]) => {
          const tone = confidenceTone(lin.confidence);
          return (
            <tr key={target}>
              <td className="font-medium">{target}</td>
              <td>{lin.source_column ? <code className="rounded bg-canvas px-1.5 py-0.5 text-[12px]">{lin.source_column}</code> : <span className="text-ink-subtle">— (default)</span>}</td>
              <td className="text-ink-muted">{lin.default_value || "—"}</td>
              <td>
                {(lin.rules || []).length === 0 ? <span className="text-ink-subtle">—</span> : (
                  <div className="flex flex-wrap gap-1">
                    {(lin.rules || []).map((r: any, i: number) =>
                      <Pill key={i} tone="brand">{r.rule_type}</Pill>)}
                  </div>
                )}
              </td>
              <td className="font-mono text-xs tabular-nums">
                <span className={
                  tone === "success" ? "text-success" :
                  tone === "warning" ? "text-warning" : "text-danger"
                }>{Math.round(lin.confidence * 100)}%</span>
              </td>
              <td><Pill tone={lin.status === "approved" ? "success" : "neutral"}>{lin.status}</Pill></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  const renderCluster = (cl: DuplicateCluster) => {
    const chosen = survivors[cl.cluster_key] ?? cl.decision?.survivor_key ?? null;
    const working = !!busy[cl.cluster_key];
    const err = rowError[cl.cluster_key];
    const decision = cl.decision;
    const learned = decision?.source === "learned";

    // What each row's fate WOULD be. Driven by the saved verdict when there is
    // one, otherwise by the pending survivor selection — so picking a radio shows
    // immediately which rows drop, instead of leaving the list looking untouched
    // until the file is regenerated.
    const allKeys = cl.members.map(m => m.key).filter(Boolean) as string[];
    const savedKeep = decision?.verdict === "keep_subset"
      ? decision.keep_keys ?? [] : null;
    // A pending tick-list wins over the saved verdict — the user is editing it.
    const keepSet: string[] | null = subsetKeeps[cl.cluster_key] ?? savedKeep;

    const pendingVerdict: DuplicateVerdict | null = keepSet
      ? "keep_subset"
      : decision?.verdict ?? (chosen ? "keep_survivor" : null);
    const fateOf = (memberKey: string | null | undefined): "keep" | "drop" | null => {
      if (!memberKey) return null;
      if (keepSet) return keepSet.includes(memberKey) ? "keep" : "drop";
      if (!pendingVerdict) return null;
      if (pendingVerdict === "keep_all") return "keep";
      if (pendingVerdict === "exclude") return "drop";
      return memberKey === chosen ? "keep" : "drop";   // merge | keep_survivor
    };
    const survivingRows =
      keepSet ? keepSet.length
        : pendingVerdict === "keep_all" ? cl.members.length
        : pendingVerdict === "exclude" ? 0
        : pendingVerdict ? 1 : cl.members.length;
    const projected = !!pendingVerdict && survivingRows !== cl.members.length;

    const toggleKeep = (memberKey: string) =>
      setSubsetKeeps(prev => {
        // First tick starts from "everything kept", so unticking reads as
        // removing rows rather than building a selection from nothing.
        const cur = prev[cl.cluster_key] ?? savedKeep ?? allKeys;
        const next = cur.includes(memberKey)
          ? cur.filter(k => k !== memberKey)
          : [...cur, memberKey];
        return { ...prev, [cl.cluster_key]: next };
      });
    const clearSubset = () =>
      setSubsetKeeps(prev => {
        const next = { ...prev };
        delete next[cl.cluster_key];
        return next;
      });

    return (
      <div key={cl.cluster_key} className="rounded-lg border border-line bg-white">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone={cl.confidence >= 0.92 ? "danger" : cl.confidence >= 0.8 ? "warning" : "neutral"}>
              {Math.round(cl.confidence * 100)}% match
            </Pill>
            <span className="text-xs text-ink-muted">
              {plural(cl.size, "record")}
              {projected && (
                <span className="ml-1 font-medium text-brand-dark">
                  → {survivingRows} in the file
                  {!decision && " (not saved yet)"}
                </span>
              )}
            </span>
            {cl.verdict && (
              <Pill tone={cl.verdict === "same" ? "danger" : cl.verdict === "different" ? "success" : "neutral"}>
                AI: {cl.verdict}
              </Pill>
            )}
            {decision && (
              <Pill tone={learned ? "info" : DUP_TONE[decision.verdict]}>
                {DUP_LABEL[decision.verdict]}
                {learned ? " · learned from an earlier run" : " · decided here"}
              </Pill>
            )}
          </div>
          <span className="text-[11px] text-ink-subtle">on {cl.fields.join(", ")}</span>
        </div>

        {/* Two different tax registrations across a cluster usually mean two legal
            entities, and merging would pick one of them arbitrarily. Flagged, not
            auto-split: it can also be one source recording the number wrong, and
            splitting would hide that duplicate. */}
        {(cl.id_conflicts?.length ?? 0) > 0 && (
          <div className="border-b border-line bg-warning-subtle px-3 py-1.5 text-[11px] text-warning">
            {cl.id_conflicts!.map(c => (
              <div key={c.column}>
                <strong>{c.values.length} different {c.column} values</strong> in this
                group ({c.values.slice(0, 4).join(", ")}
                {c.values.length > 4 ? ", …" : ""}) — likely separate entities.
                Merging keeps only one of them.
              </div>
            ))}
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="table-shell">
            <thead>
              <tr>
                <th className="whitespace-nowrap">Keep</th>
                <th className="whitespace-nowrap">Survivor</th>
                <th>Row</th>
                {idCols.map(f => <th key={f} className="whitespace-nowrap">{f}</th>)}
              </tr>
            </thead>
            <tbody>
              {cl.members.map(m => {
                // Row keys are per-row (identity hash + content + occurrence), so
                // this matches exactly one row. It used to be the identity hash
                // alone, which every identical-name twin shared — selecting one
                // lit up three and "keep survivor" could not say which to keep.
                const isSurvivor = !!m.key && m.key === chosen;
                const fate = fateOf(m.key);
                return (
                  <tr
                    key={`${cl.cluster_key}-${m.key ?? m.row}`}
                    className={cn(
                      isSurvivor && "bg-brand-subtle/60",
                      fate === "drop" && "text-ink-subtle line-through opacity-60",
                    )}
                  >
                    <td>
                      {/* Ticking rows expresses "keep these, drop the rest" — the
                          verdict a partly-duplicated cluster needs, which merge
                          (1 row) and keep-all (every row) cannot describe. */}
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 accent-brand"
                        checked={fate !== "drop"}
                        disabled={!m.key || working}
                        title="Keep this record in the output"
                        onChange={() => m.key && toggleKeep(m.key)}
                      />
                    </td>
                    <td>
                      <label className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                        <input
                          type="radio"
                          name={`survivor-${cl.cluster_key}`}
                          className="h-3.5 w-3.5 accent-brand"
                          checked={isSurvivor}
                          disabled={!m.key || working}
                          onChange={() => {
                            if (!m.key) return;
                            setSurvivors(prev => ({ ...prev, [cl.cluster_key]: m.key as string }));
                            setErrorKey(cl.cluster_key, null);
                          }}
                        />
                        {fate === "keep" && (
                          <span className="font-medium text-brand-dark">
                            {decision?.verdict === "merge" ? "merged into" : "keep"}
                          </span>
                        )}
                        {fate === "drop" && (
                          <span className="font-medium text-ink-subtle">drop</span>
                        )}
                      </label>
                    </td>
                    <td className="text-ink-muted">{m.row + 1}</td>
                    {idCols.map(f => (
                      <td key={f} className="whitespace-nowrap">{String(m.values?.[f] ?? "")}</td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {cl.ai_reason && (
          <div className="border-t border-line px-3 py-1.5 text-[11px] text-ink-muted">
            <Sparkles className="mr-1 inline h-3 w-3" />{cl.ai_reason}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t border-line bg-canvas px-3 py-2">
          <Button variant="primary" disabled={!chosen || working}
                  title={chosen ? undefined : "Nominate a survivor first"}
                  onClick={() => void decideCluster(cl, "merge")}>
            Merge
          </Button>
          <Button variant="secondary" disabled={!chosen || working}
                  title={chosen ? undefined : "Nominate a survivor first"}
                  onClick={() => void decideCluster(cl, "keep_survivor")}>
            Keep survivor only
          </Button>
          {keepSet && keepSet.length > 0 && keepSet.length < cl.members.length && (
            <Button variant="primary" disabled={working}
                    onClick={() => void decideCluster(cl, "keep_subset", keepSet)}>
              Keep selected ({keepSet.length})
            </Button>
          )}
          <Button variant="secondary" disabled={working}
                  onClick={() => { clearSubset(); void decideCluster(cl, "keep_all"); }}>
            Keep all
          </Button>
          <Button variant="danger" disabled={working}
                  onClick={() => void decideCluster(cl, "exclude")}>
            Exclude all
          </Button>
          {/* A learned verdict lives on the earlier conversion — there is nothing to
              undo here; deciding this cluster overrides it for this run instead. */}
          {decision && !learned && (
            <Button variant="ghost" disabled={working} onClick={() => void undoCluster(cl)}>
              <RotateCcw className="h-4 w-4" /> Undo
            </Button>
          )}
          {/* Ticks are unsaved state, so Undo (which deletes a saved decision)
              would not clear them — this does. */}
          {subsetKeeps[cl.cluster_key] !== undefined && (
            <Button variant="ghost" disabled={working} onClick={clearSubset}>
              <RotateCcw className="h-4 w-4" /> Reset ticks
            </Button>
          )}
          {learned && (
            <span className="text-[11px] text-ink-subtle">
              Carried over from an earlier run — pick an action to override it here.
            </span>
          )}
          {!chosen && !learned && (
            <span className="text-[11px] text-ink-subtle">
              Pick a survivor to enable Merge / Keep survivor only.
            </span>
          )}
          {err && <span className="text-[11px] font-medium text-danger">{err}</span>}
        </div>
      </div>
    );
  };

  const renderDupes = () => (
    <CardBody>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <p className="max-w-2xl text-xs text-ink-muted">
          Records likely to be the <span className="font-medium">same entity</span> despite different keys/names —
          what the exact-key de-duplication can't catch.
          {dupes?.anchor && <> Matched on <code className="rounded bg-canvas px-1 py-0.5">{dupes.anchor}</code> + identity fields.</>}
        </p>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => void loadDupes(false)} loading={dupLoading}>
            <Copy className="h-4 w-4" /> Re-scan
          </Button>
          <Button variant="secondary" onClick={() => { setDupAi(true); void loadDupes(true); }} loading={dupLoading && dupAi}>
            <Sparkles className="h-4 w-4" /> Adjudicate with AI
          </Button>
        </div>
      </div>

      {dupLoading ? <PageLoader /> : !dupes || dupes.clusters.length === 0 ? (
        <EmptyState
          title="No likely duplicates found"
          description={dupError || dupes?.note
            || `Scanned ${dupes?.rows_scanned ?? 0} records — no near-duplicate entities above the match threshold.`}
        />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]">
            <Pill tone="warning">{plural(dupes.cluster_count ?? dupes.clusters.length, "suspected group")}</Pill>
            <Pill tone="neutral">{plural(dupes.duplicate_rows ?? 0, "record")}</Pill>
            <Pill tone="neutral">{dupes.rows_scanned} scanned</Pill>
            <Pill tone="success">{dupes.decided_count ?? 0} decided</Pill>
            {/* Both counters cover the RETURNED groups only. Saying plain "0
                undecided" next to "340 suspected groups" reads as fully reviewed
                when 240 were never sent. */}
            <Pill tone={undecidedDupes > 0 ? "danger" : "neutral"}>
              {undecidedDupes} undecided
              {(dupes.hidden_count ?? 0) > 0 && ` of ${dupes.returned_count ?? dupes.clusters.length} shown`}
            </Pill>
            {(dupes.hidden_count ?? 0) > 0 && (
              <Pill tone="warning">{dupes.hidden_count} not reviewed</Pill>
            )}
            {dupes.ai_used && <Pill tone="brand">AI-adjudicated</Pill>}
          </div>

          {/* The scanner returns at most `max_clusters`; `cluster_count` is the true
              total. Without this the decided/undecided pills describe only the
              visible slice and the list looks complete when it is not. */}
          {(dupes.hidden_count ?? 0) > 0 && (
            <div className="mb-3 rounded-lg border border-line bg-warning-subtle px-3 py-2 text-[11px] text-warning">
              Showing the {dupes.returned_count ?? dupes.clusters.length} highest-confidence
              groups. <strong>{dupes.hidden_count} more are not listed</strong> and cannot be
              decided here — the counters above cover the visible groups only. Raise the
              limit to review them all.
            </div>
          )}

          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-2">
            <Button
              variant="secondary"
              loading={bulkBusy}
              disabled={highConfidenceTargets.length === 0}
              onClick={() => setConfirm({
                title: "Merge all high-confidence groups",
                confirmLabel: `Merge ${highConfidenceTargets.length}`,
                tone: "primary",
                body: (
                  <div className="space-y-2 text-sm text-ink-muted">
                    <p className="text-ink">
                      {plural(highConfidenceTargets.length, "undecided group")} at {Math.round(HIGH_CONFIDENCE * 100)}% match or
                      above will be merged into a golden record.
                    </p>
                    <p>
                      Groups where you nominated a survivor keep that record as the base; the rest
                      collapse field-by-field (first non-blank value wins). Groups you already decided
                      are left untouched. You can undo any of them afterwards.
                    </p>
                  </div>
                ),
                onConfirm: () => { setConfirm(null); void mergeHighConfidence(highConfidenceTargets); },
              })}
            >
              <Wand2 className="h-4 w-4" /> Merge all high-confidence (≥{Math.round(HIGH_CONFIDENCE * 100)}%)
              {highConfidenceTargets.length > 0 && ` · ${highConfidenceTargets.length}`}
            </Button>
            <Button
              variant="ghost"
              loading={bulkBusy}
              onClick={() => setConfirm({
                title: "Clear all decisions",
                confirmLabel: "Clear everything",
                tone: "danger",
                body: (
                  <div className="space-y-2 text-sm text-ink-muted">
                    <p className="text-ink">
                      Every duplicate and cleansing decision recorded on this conversion will be deleted.
                    </p>
                    <p>
                      Verdicts learned from an earlier run for this client + object stay in place —
                      they belong to that run, not this one.
                    </p>
                  </div>
                ),
                onConfirm: () => { setConfirm(null); void clearAllDecisions(); },
              })}
            >
              <RotateCcw className="h-4 w-4" /> Clear all decisions
            </Button>
            {bulkError && <span className="text-[11px] font-medium text-danger">{bulkError}</span>}
          </div>

          <div className="space-y-3">{dupes.clusters.map(renderCluster)}</div>
        </>
      )}
    </CardBody>
  );

  const renderCleansingRules = () => {
    if (!profile) return null;
    const active = profile.profile.families;
    return (
      <div className="mb-4 rounded-lg border border-line bg-canvas p-3">
        <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-xs font-medium text-ink">Cleansing rules</p>
            <p className="mt-0.5 max-w-2xl text-[11px] text-ink-muted">
              Standing rules applied to every value at generation, after duplicate
              decisions — so only the rows that actually ship get cleansed.
              {profile.is_default && " Using the safe defaults."}
            </p>
          </div>
          <Button variant="secondary" loading={previewBusy}
                  onClick={() => void runCleansePreview(
                    active.length ? active : undefined)}>
            <Sparkles className="h-4 w-4" /> Preview changes
          </Button>
        </div>

        <div className="flex flex-wrap gap-2">
          {profile.families.map(f => {
            const on = active.includes(f.key);
            return (
              <label
                key={f.key}
                className={cn(
                  "flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 text-[11px] transition",
                  on ? "border-brand bg-brand-subtle/40" : "border-line bg-white",
                  profileBusy && "pointer-events-none opacity-60",
                )}
              >
                <input
                  type="checkbox"
                  className="mt-0.5 h-3.5 w-3.5 accent-brand"
                  checked={on}
                  disabled={profileBusy}
                  onChange={() => void toggleFamily(f.key)}
                />
                <span>
                  <span className="font-medium text-ink">{f.label}</span>
                  {/* An unsafe family REWRITES business values. Saying so on the
                      control matters more than in a doc nobody opens — "Acme
                      Limited" becoming "Acme Ltd" changes a legal name in a
                      client-facing FBDI. */}
                  {!f.safe && (
                    <span className="ml-1 text-warning">· rewrites values</span>
                  )}
                  <span className="block text-ink-subtle">
                    {f.key === "whitespace_punct" && "Trims, collapses runs, drops trailing dots"}
                    {f.key === "special_chars" && "Control/zero-width chars, smart quotes, dashes"}
                    {f.key === "case" && "Title case for names, upper for codes"}
                    {f.key === "legal_suffix" && "Ltd. / Limited → one canonical form"}
                  </span>
                </span>
              </label>
            );
          })}
        </div>

        {profileError && (
          <p className="mt-2 text-[11px] text-danger">{profileError}</p>
        )}

        {cleansePreview && (
          <div className="mt-3 rounded-lg border border-line bg-white p-2.5">
            <p className="mb-2 text-[11px] text-ink-muted">
              <span className="font-medium text-ink">
                {cleansePreview.total_changes.toLocaleString()} value(s)
              </span>{" "}
              across {plural(cleansePreview.fields_affected, "field")} would change
              {typeof cleansePreview.rows_scanned === "number"
                && ` (${cleansePreview.rows_scanned.toLocaleString()} rows scanned)`}.
              Nothing has been written — this is a dry run. Edit any{" "}
              <span className="font-medium text-ink">After</span> value and Save to pin
              your own result for that value, or <span className="font-medium text-ink">Skip
              field</span> to stop one rule touching one column.
            </p>

            {/* Skipped fields are invisible once applied — the rule simply stops
                firing — so they need a way back. */}
            {Object.entries(profile.profile.per_field ?? {})
              .filter(([, fams]) => fams.length < profile.profile.families.length)
              .length > 0 && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px]">
                <span className="text-ink-muted">Rules skipped on:</span>
                {Object.entries(profile.profile.per_field ?? {})
                  .filter(([, fams]) => fams.length < profile.profile.families.length)
                  .map(([field]) => (
                    <button
                      key={field}
                      disabled={profileBusy}
                      className="rounded-full border border-line bg-white px-2 py-0.5 hover:border-danger hover:text-danger"
                      title="Restore every enabled rule on this field"
                      onClick={() => {
                        const next = { ...(profile.profile.per_field ?? {}) };
                        delete next[field];
                        void saveProfile({ ...profile.profile, per_field: next });
                      }}
                    >
                      {field} ✕
                    </button>
                  ))}
              </div>
            )}
            {cleansePreview.findings.length === 0 ? (
              <p className="text-[11px] text-ink-subtle">
                No value would change under these rules.
              </p>
            ) : (
              <div className="max-h-96 overflow-y-auto">
                <table className="table-shell">
                  <thead>
                    <tr>
                      <th>Field</th><th>Rule</th><th>Rows</th>
                      <th>Before</th><th>After (editable)</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {cleansePreview.findings.slice(0, 40).map(f => {
                      const rowKey = `${f.field}|${f.rule}`;
                      const before = f.examples[0]?.before ?? "";
                      const suggested = f.examples[0]?.after ?? "";
                      const pinned = profile?.profile.value_overrides?.[f.field]?.[before];
                      const draft = edits[rowKey] ?? pinned ?? suggested;
                      const dirty = draft !== (pinned ?? suggested);
                      const isOverride = f.rule === "override";
                      return (
                        <tr key={rowKey}>
                          <td className="whitespace-nowrap font-medium">{f.field}</td>
                          <td className="whitespace-nowrap text-ink-muted">
                            {f.label ?? f.rule}
                          </td>
                          <td className="text-ink-muted">{f.count.toLocaleString()}</td>
                          <td className="max-w-xs truncate text-ink-subtle line-through">
                            {before}
                          </td>
                          <td>
                            {/* Editing the result is the fastest correction: it
                                fixes THIS value without switching off a family
                                that is right about the rest of the column. */}
                            <input
                              className="w-56 rounded border border-line px-1.5 py-0.5 text-[11px]"
                              value={draft}
                              disabled={profileBusy}
                              onChange={e =>
                                setEdits(prev => ({ ...prev, [rowKey]: e.target.value }))}
                            />
                          </td>
                          <td className="whitespace-nowrap">
                            {dirty && (
                              <button
                                className="mr-2 text-[11px] font-medium text-brand-dark hover:underline"
                                disabled={profileBusy}
                                onClick={() => {
                                  overrideValue(f.field, before, draft);
                                  setEdits(prev => {
                                    const n = { ...prev }; delete n[rowKey]; return n;
                                  });
                                }}
                              >
                                Save
                              </button>
                            )}
                            {pinned !== undefined && !dirty && (
                              <button
                                className="mr-2 text-[11px] text-ink-muted hover:underline"
                                disabled={profileBusy}
                                onClick={() => clearOverride(f.field, before)}
                              >
                                Unpin
                              </button>
                            )}
                            {!isOverride && (
                              <button
                                className="text-[11px] text-ink-muted hover:underline"
                                disabled={profileBusy}
                                title={`Stop "${f.label ?? f.rule}" touching ${f.field}`}
                                onClick={() =>
                                  skipRuleForField(f.field, f.rule as CleansingFamily)}
                              >
                                Skip field
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {cleansePreview.findings.length > 40 && (
                  <p className="mt-1 text-[11px] text-ink-subtle">
                    Showing the 40 highest-impact of {cleansePreview.findings.length} field/rule pairs.
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  const renderCleansing = () => (
    <CardBody>
      {renderCleansingRules()}
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <p className="max-w-2xl text-xs text-ink-muted">
          Data-quality findings raised against this interface. <span className="font-medium">Apply</span> lets the
          suggested fix run at generation time; <span className="font-medium">Ignore</span> records that the value is
          correct as-is. Anything left undecided is carried through untouched.
        </p>
        <Button variant="secondary" onClick={() => void loadReview()} loading={reviewLoading}>
          <Copy className="h-4 w-4" /> Refresh
        </Button>
      </div>

      {reviewLoading ? <PageLoader /> : reviewError ? (
        <EmptyState title="Couldn't load the review bundle" description={reviewError} />
      ) : cleansingCount === 0 ? (
        // This list is NOT the rules panel above. It holds ValidationIssue rows
        // profiled from the SOURCE dataset, which only exist once someone runs
        // the check — so "none found" almost always means "never run", and the
        // old empty state said "run validation" without offering a way to.
        <EmptyState
          title="No cleansing findings — the check hasn't been run"
          description={
            "These are data-quality observations profiled from the source file "
            + "(high nulls, mixed types, odd patterns), separate from the standing "
            + "cleansing rules above. Run it to populate them."
          }
          action={
            <Button variant="secondary" loading={profileBusy}
                    onClick={() => void runProfileCleansing()}>
              <Sparkles className="h-4 w-4" /> Run cleansing check
            </Button>
          }
        />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]">
            <Pill tone="neutral">{plural(cleansingCount, "finding")}</Pill>
            <Pill tone={openCleansing > 0 ? "warning" : "success"}>{openCleansing} undecided</Pill>
            <Pill tone="neutral">{plural(review?.duplicate_decisions ?? 0, "duplicate decision")}</Pill>
          </div>

          <div className="space-y-4">
            {cleansingGroups.map(group => (
              <div key={group.severity}>
                <div className="mb-2 flex items-center gap-2">
                  <Pill tone={severityTone(group.severity)}>{group.severity}</Pill>
                  <span className="text-[11px] text-ink-subtle">{plural(group.items.length, "finding")}</span>
                </div>
                <div className="space-y-2">
                  {group.items.map(f => {
                    const working = !!busy[f.key];
                    const err = rowError[f.key];
                    return (
                      <div key={f.key} className="rounded-lg border border-line bg-white px-3 py-2">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-sm font-medium text-ink">
                                {f.field_name || f.category || "Record"}
                              </span>
                              {f.issue_type && <Pill tone="neutral">{f.issue_type}</Pill>}
                              {f.auto_fixable && <Pill tone="brand">auto-fixable</Pill>}
                              {f.occurrences > 1 && <Pill tone="neutral">{f.occurrences} findings</Pill>}
                              <span className="text-[11px] text-ink-subtle">
                                {plural(f.impacted_count ?? 0, "row")} impacted
                              </span>
                              {f.verdict && (
                                <Pill tone={f.verdict === "apply" ? "success" : "neutral"}>
                                  {f.verdict === "apply" ? "Fix will be applied" : "Ignored"}
                                </Pill>
                              )}
                            </div>
                            <p className="mt-1 text-xs text-ink-muted">{f.message}</p>
                            {f.suggested_fix && (
                              <p className="mt-1 text-xs text-ink-muted">
                                <span className="font-medium text-ink">Suggested fix:</span>{" "}
                                <code className="rounded bg-canvas px-1 py-0.5 text-[11px]">{f.suggested_fix}</code>
                              </p>
                            )}
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            <Button
                              variant={f.verdict === "apply" ? "primary" : "secondary"}
                              disabled={working}
                              onClick={() => void decideFinding(f, "apply")}
                            >Apply</Button>
                            <Button
                              variant={f.verdict === "ignore" ? "primary" : "secondary"}
                              disabled={working}
                              onClick={() => void decideFinding(f, "ignore")}
                            >Ignore</Button>
                            {f.verdict && (
                              <Button variant="ghost" disabled={working}
                                      onClick={() => void decideFinding(f, null)}>
                                <RotateCcw className="h-4 w-4" /> Undo
                              </Button>
                            )}
                          </div>
                        </div>
                        {err && <div className="mt-1 text-[11px] font-medium text-danger">{err}</div>}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </CardBody>
  );

  const renderTab = () => {
    if (tab === "dupes") return renderDupes();
    if (tab === "cleansing") return renderCleansing();
    if (data === null) return <PageLoader />;
    if (tab === "data") return renderData(data);
    return renderLineage(data);
  };

  return (
    <>
      <Link to={`/projects/${pid}`} className="mb-3 inline-flex items-center gap-1 text-xs text-ink-muted hover:text-ink">
        <ArrowLeft className="h-3 w-3" /> Back to Project
      </Link>
      <PageTitle
        title="Output Preview"
        subtitle={`${project.name} → ${project.template_name}`}
        right={<>
          <Button variant="secondary" onClick={generate} loading={generating}>
            <FileOutput className="h-4 w-4" /> Re-generate
          </Button>
          <Button variant="secondary" onClick={handleDownload} loading={downloading}>
            <Download className="h-4 w-4" /> This file (.csv)
          </Button>
          <Button variant="primary" onClick={handleDownloadAll} loading={downloadingAll}>
            <FolderDown className="h-4 w-4" /> Download all FBDI (.zip)
          </Button>
        </>}
      />

      {decisionsDirty && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warning bg-warning-subtle px-4 py-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div>
              <div className="text-sm font-semibold text-ink">
                Decisions changed since this file was generated — re-generate
              </div>
              <div className="text-xs text-ink-muted">
                The file on disk still reflects the previous duplicate/cleansing verdicts.
                Download will hand out stale data until you re-generate.
              </div>
            </div>
          </div>
          <Button variant="primary" onClick={generate} loading={generating}>
            <FileOutput className="h-4 w-4" /> Re-generate now
          </Button>
        </div>
      )}

      <Card>
        <Tabs
          value={tab}
          onChange={(v) => { setTab(v); if (v === "dupes" && dupes === null && !dupError) void loadDupes(false); }}
          items={[
            { value: "data", label: "Converted Data", count: data?.total_rows },
            { value: "lineage", label: "Lineage", count: data ? Object.keys(data.lineage).length : 0 },
            { value: "dupes", label: "Duplicate suspects", count: dupes?.cluster_count },
            { value: "cleansing", label: "Cleansing", count: cleansingCount },
          ]}
        />
        {renderTab()}
      </Card>

      <Modal
        open={!!confirm}
        onClose={() => setConfirm(null)}
        title={confirm?.title ?? ""}
        size="sm"
        footer={<>
          <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
          <Button variant={confirm?.tone ?? "primary"} onClick={() => confirm?.onConfirm()}>
            {confirm?.confirmLabel ?? "Confirm"}
          </Button>
        </>}
      >
        {confirm?.body}
      </Modal>
    </>
  );
};
