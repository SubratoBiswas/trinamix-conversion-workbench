# AI Differentiators — Status & Roadmap

How AI is leveraged in the Conversion Workbench, what was just added, and the design for the two large epics still to build.

## Where AI sits today

AI runs as the **last layer** of a precedence stack — golden record → learnings → mapping workbook → user rule → deterministic → default → **AI** — so it only fills the residual and every action is human-reviewable with provenance. It is used for: mapping suggestions, semantic plausibility guard + candidate vetting, value/LOV normalization, learn-from-example and natural-language steering, blank-fill, cleansing suggestions, and inferred defaults. A learning loop captures every analyst correction, so AI reliance drops each engagement.

## Wave 1 — shipped (this build)

- **AI survivorship (golden record):** in the multi-source merge, each field takes the first non-blank value across sources in priority order — the top source wins per field, its blanks back-filled from lower sources. (`merge_dedupe.merge_dedupe(..., survivorship=True)`, unit-tested.)
- **AI-drafted DQ rules:** `POST /dq-rules/ai-propose` — Claude proposes validation + cleansing rules from FBDI field metadata + a data sample; returned for review, saved via `/dq-rules/bulk`. Deterministic fallback when AI is unavailable. UI: "AI suggest rules" on the Validation & Cleansing page.
- **Predictive pre-load report:** `GET /conversions/{id}/preload-report` — validates the merged frame without writing a file and returns a plain-English "what Oracle will reject and how to fix" summary grouped by issue type.
- **Load remediation + reconciliation:** `POST /load-runs/{id}/explain-errors` (root cause + fix per error, pattern-based) and `GET /conversions/{id}/reconciliation` (source vs merged-output vs load counts, with narrative).

## Epic A — Agentic end-to-end conversion

**Goal:** point the tool at source file(s) + a target module; an agent proposes the full conversion, dry-runs it, and iterates at human checkpoints — instead of field-by-field.

**Design (phased):**
1. *Orchestrator* — a server-side agent loop (reuse the existing AI provider + tool functions as callable "skills": map, apply-learnings, generate, preload-validate, reconcile).
2. *Plan step* — agent drafts mappings + rules + defaults for every interface object, citing which layer each decision came from.
3. *Dry-run step* — generate to the merged frame, run the pre-load report, surface only exceptions.
4. *Checkpoint UI* — the analyst approves/edits at plan, dry-run, and pre-load gates; approvals become learnings.
5. *Guardrails* — never auto-load; every agent action logged with provenance and reversible.

**Effort:** large (multi-week). **Depends on:** Wave 1 (uses preload-report + reconciliation as the agent's feedback signals).

## Epic B — Conversational copilot (project-wide)

**Goal:** a chat assistant that both explains and operates the tool — "why is Invoice Match Option blank?", "regenerate supplier without headers", "which suppliers failed and why".

**Design (phased):**
1. *Read-only Q&A first* — extend the existing `copilot` router; ground answers in the project's mappings, learnings, DQ report, and reconciliation (retrieval over the conversion's own data).
2. *Action tools* — expose safe, confirmable actions (regenerate, toggle header, apply a rule, re-run AI on a field) as copilot tools; each requires explicit confirmation.
3. *Explainability* — every answer cites provenance (which precedence layer / rule / source decided a value).
4. *Guardrails* — no destructive or load actions without confirmation; all actions audited.

**Effort:** medium-large. **Depends on:** the provenance data Wave 1 already exposes.

## Positioning (why it beats market tools)

Learns per client and compounds; Oracle-Fusion-native (LOVs, interface dependencies, FBDI load format); and every AI action is explained and reversible — an auditable copilot, not an opaque converter.
