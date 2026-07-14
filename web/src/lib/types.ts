export interface UploadResponse {
  session_id: string;
  filename: string;
  // PLAN-persistent-draft-uploads.md (Phase A): the upload endpoint
  // creates a draft `runs` row and returns its id so the frontend can
  // navigate to a shareable `/run/{run_id}` URL immediately. Nullable
  // because the backend best-effort path returns null when the audit
  // DB write failed — the UI still gets a working session even if
  // History won't show this run.
  run_id: number | null;
}

export interface SettingsResponse {
  model: string;
  proxy_url: string;
  api_key_set: boolean;
  api_key_preview: string;
}

export type EventPhase =
  | "starting"           // Multi-agent run initializing
  | "scouting"           // Scout analyzing PDF structure
  | "started"            // Notes agent / sub-agent first status event
  | "reading_template"
  | "viewing_pdf"
  | "writing_notes"      // Notes agent payload emission phase
  | "filling_workbook"
  | "verifying"
  | "cancelled"          // Agent was aborted by user
  | "complete";

// SSE event types — extended for streaming architecture (P0)
export type SSEEventType =
  | "status"           // Phase transitions
  | "thinking_delta"   // Streaming thinking token chunk
  | "thinking_end"     // Thinking block complete
  | "text_delta"       // Streaming model text chunk
  | "tool_call"        // Tool invocation (before execution)
  | "tool_result"      // Tool completion (after execution)
  | "token_update"     // Running token totals
  | "error"            // Agent/system error
  | "complete"         // Single-agent run finished (legacy) OR per-agent completion (multi-agent)
  | "run_complete"     // Final aggregate event for multi-agent runs
  // PLAN-stop-and-validation-visibility Phase 2: emitted from the
  // Stop-All cancel handler when at least one per-statement workbook
  // already landed on disk. Lets the frontend show a "Saved partial
  // workbook" banner alongside the "Run cancelled" error.
  | "partial_merge"
  // PLAN-stop-and-validation-visibility Phase 5: per-pass progress
  // events for the cross-check engine so the Validator tab fills
  // rows in as they're confirmed instead of waiting for run_complete.
  | "cross_check_start"      // pass-level prelude: phase + total
  | "cross_check_result"     // one per check, in order
  | "cross_check_complete"   // pass-level summary: counts
  // PLAN-stop-and-validation-visibility Phase 6: coordinator-level
  // stage boundaries so the UI can label the current activity
  // (extracting / merging / cross_checking / correcting /
  // re_checking / validating_notes / done).
  | "pipeline_stage"
  // Scout-quality warnings surfaced before / during fan-out. Both are
  // run-level (no agent_id) and feed the same warnings banner:
  //  - scout_warnings: completeness probe findings (server, pre-flight).
  //  - scale_conflict: scale-unit reconciliation conflict (coordinator).
  | "scout_warnings"
  | "scale_conflict";

// Every multi-agent event carries these routing fields inside `data` (the
// backend stamps them in agent_runner.build_agent_event). We keep them in `data`
// rather than hoisting to the event root so the runtime shape matches the
// wire format for both live SSE and persisted history — no parse-time
// transform is needed.
export interface AgentRouting {
  agent_id?: string;
  agent_role?: string;
}

// Phase 6 decision: Option A (discriminated union) with the routing fields
// placed per-data interface instead of hoisted to the event root. Narrowing
// on `event.event` yields a typed `event.data`, and every `event.data`
// includes the optional `agent_id`/`agent_role` pair, which eliminates the
// `as unknown as Record<string, unknown>` casts the earlier single-shape
// type required.
interface SSEEventDataMap {
  status: StatusData & AgentRouting;
  thinking_delta: ThinkingDeltaData & AgentRouting;
  thinking_end: ThinkingEndData & AgentRouting;
  text_delta: TextDeltaData & AgentRouting;
  tool_call: ToolCallData & AgentRouting;
  tool_result: ToolResultData & AgentRouting;
  token_update: TokenData & AgentRouting;
  error: ErrorData & AgentRouting;
  complete: (CompleteData | AgentCompleteData) & AgentRouting;
  run_complete: RunCompleteData & AgentRouting;
  // partial_merge has no agent_id (it's a coordinator-level event)
  // but the routing intersection is harmless — keeps the union shape
  // uniform across all event types.
  partial_merge: PartialMergeData & AgentRouting;
  // PLAN-stop-and-validation-visibility Phase 5 — cross-check progress.
  cross_check_start: CrossCheckStartData & AgentRouting;
  cross_check_result: CrossCheckResultEventData & AgentRouting;
  cross_check_complete: CrossCheckCompleteData & AgentRouting;
  // PLAN-stop-and-validation-visibility Phase 6 — pipeline stage label.
  pipeline_stage: PipelineStageData & AgentRouting;
  // Scout-quality warnings (run-level; no agent_id — see SSEEventType).
  scout_warnings: ScoutWarningsData & AgentRouting;
  scale_conflict: ScaleConflictData & AgentRouting;
}

export type SSEEvent = {
  [K in SSEEventType]: {
    event: K;
    data: SSEEventDataMap[K];
    timestamp: number;
  };
}[SSEEventType];

export interface StatusData {
  phase: EventPhase;
  message: string;
}

export interface ThinkingDeltaData {
  content: string;       // Incremental thinking text chunk
  thinking_id: string;   // Groups chunks into blocks
}

export interface ThinkingEndData {
  thinking_id: string;
  summary: string;        // One-line summary (first ~80 chars)
  full_length: number;    // Character count of full thinking block
  duration_ms?: number;   // Actual reasoning time (server-measured, optional for backwards-compat)
}

export interface TextDeltaData {
  content: string;        // Incremental text chunk
}

export interface ToolCallData {
  tool_name: string;
  tool_call_id: string;   // For pairing with result
  args: Record<string, unknown>;
}

export interface ToolResultData {
  tool_name: string;
  tool_call_id: string;   // Matches the tool_call
  result_summary: string;
  duration_ms: number;
}

export interface TokenData {
  prompt_tokens: number;
  completion_tokens: number;
  thinking_tokens: number;
  cumulative: number;
  cost_estimate: number;
}

/** Phase 6.2 error taxonomy. `fatal` terminates the run; `recoverable` and
 *  `advisory` leave it running through to `run_complete`. Absent on legacy
 *  event shapes — the reducer then falls back to the `type`-presence
 *  heuristic (untyped == terminal). */
export type ErrorBucket = "advisory" | "recoverable" | "fatal";

export interface ErrorData {
  message: string;
  traceback?: string;
  /** Discriminator for coordinator/sub-pass error shapes (`merge_failed`,
   *  `cross_check_exception`, `canonical_reexport_failed`,
   *  `canonical_export_degraded`, `reviewer_*`, `validator_*`). Absent on
   *  legacy untyped transport/validation errors. */
  type?: string;
  /** Phase 6.2: see {@link ErrorBucket}. */
  bucket?: ErrorBucket;
}

/** PLAN-stop-and-validation-visibility Phase 5.1.
 *  Phase label distinguishes the initial cross-check pass from the
 *  post-correction re-run so the UI can render two separate progress
 *  blocks ("Cross-checks…" and "Re-checking after correction…").
 */
export type CrossCheckPhase = "initial" | "post_correction";

/** Pass-level prelude — emitted once at the start of each cross-check
 *  pass with the total checks the engine plans to run. */
export interface CrossCheckStartData {
  phase: CrossCheckPhase;
  total: number;
}

/** Per-check progress payload. ``index`` is 0-based and ``total``
 *  matches the start event so the frontend can render "3 / 12". */
export interface CrossCheckResultEventData {
  phase: CrossCheckPhase;
  index: number;
  total: number;
  name: string;
  status: "passed" | "failed" | "warning" | "not_applicable" | "pending";
  expected: number | null;
  actual: number | null;
  diff: number | null;
  tolerance: number | null;
  message: string;
  // Review Workspace Step 8 — click-to-cell target (null when the check has
  // no natural anchor).
  target_sheet?: string | null;
  target_row?: number | null;
}

/** Pass-level summary — emitted once at the end of each cross-check
 *  pass with status counts so the Validator tab can render a concise
 *  rollup ("Passed 8, Failed 1, Warning 2") without re-tallying. */
export interface CrossCheckCompleteData {
  phase: CrossCheckPhase;
  passed: number;
  failed: number;
  warnings: number;
  not_applicable: number;
  pending: number;
}

/** PLAN-stop-and-validation-visibility Phase 6.
 *  Coordinator-level stage label. Distinct from per-agent EventPhase
 *  (reading_template / viewing_pdf / …) — those describe what a single
 *  agent is doing inside its own loop. ``PipelineStage`` describes the
 *  whole pipeline's current top-level activity, which the UI uses to
 *  label the dead zones between agent activity (merge → cross-check →
 *  correct → re-check → notes-validate). */
export type PipelineStage =
  | "extracting"
  | "merging"
  | "cross_checking"
  | "correcting"
  // Canonical mode runs the reviewer pass instead of the autonomous
  // correction pass (docs/Archive/PLAN-reviewer-agent.md); it gets its own label.
  | "reviewing"
  | "re_checking"
  // The notes reviewer pass (acting successor to the notes validator) — fixes
  // prose-notes findings after the merge. Replaced the old `validating_notes`
  // label; kept here too so older in-flight streams still render.
  | "reviewing_notes"
  | "validating_notes"
  | "done";

export interface PipelineStageData {
  stage: PipelineStage;
  /** Server-side timestamp (epoch seconds). */
  started_at: number;
}

/** PLAN-stop-and-validation-visibility Phase 2.2.
 *  Payload of the ``partial_merge`` SSE event. Emitted from the cancel
 *  handler when the user hits Stop All and at least one per-statement
 *  workbook is already on disk. Lets the live UI render a "Saved partial
 *  workbook" banner with the included statement list. */
export interface PartialMergeData {
  /** True when filled.xlsx was successfully merged from the survivors. */
  merged: boolean;
  /** Path to filled.xlsx on the server. Mirrored on the runs row so the
   *  History download endpoint serves the same file. */
  merged_path: string;
  /** Face statements whose per-statement workbook was on disk
   *  (e.g. ["SOFP", "SOPL"]). Empty list when nothing landed. */
  statements_included: string[];
  /** Notes templates whose workbook was on disk. */
  notes_included: string[];
  /** Face statements that were requested but never produced a file. */
  statements_missing: string[];
  /** Notes templates that were requested but never produced a file. */
  notes_missing: string[];
  /** Set when the merge attempt itself raised; null on a clean run. */
  error: string | null;
}

/** Payload of the ``scout_warnings`` SSE event — the pre-flight completeness
 *  probe's findings (unknown scale, empty/gappy inventory, missing entity). */
export interface ScoutWarningsData {
  warnings: string[];
}

/** Payload of the ``scale_conflict`` SSE event — emitted when scale-unit
 *  reconciliation finds scout's unit disagrees with the prior-year run or the
 *  declared denomination. ``severity`` is "coerced" (value reset to unknown)
 *  or "flag" (value kept, verify). */
export interface ScaleConflictData {
  severity: string;
  scout_scale_unit: string;
  resolved_scale_unit: string;
  message: string;
}

export interface CompleteData {
  success: boolean;
  // Mirror of RunCompleteData.overall_status (UX-QA #22) so the Summary card
  // renders one honest status label instead of a binary Done/"Didn't finish".
  overallStatus?: string;
  output_path: string;
  excel_path: string;
  trace_path: string;
  total_tokens: number;
  cost: number;
  statementsCompleted?: string[];
  // Actionable failure reason carried over from RunCompleteData.message
  // when the backend rejects a run before any agent starts (unknown
  // statement, invalid infopack, model setup failure, …). Null on success.
  error?: string | null;
  // Non-fatal diagnostics from a successful run — writer skips, borderline
  // fuzzy label matches, partial sub-agent coverage. Emitted by the notes
  // coordinator on success and surfaced in the terminal timeline row so
  // operators see partial-success signals instead of a false-green
  // "Completed" badge. Optional for backward compatibility — older events
  // (and face-statement agents) don't carry it.
  warnings?: string[];
  // Phase E: canonical-mode unresolved-conflict count (mirrored from
  // RunCompleteData.open_conflicts) so ResultsView can prompt the user to
  // reconcile in the Concepts page. 0 / absent in legacy mode.
  openConflicts?: number;
  // Backend run id for the completed audit row. Used by the post-run review
  // action even when the extraction began from the legacy bare "/" flow.
  runId?: number;
}

/** Per-agent completion event emitted by multi-agent runs. */
export interface AgentCompleteData {
  success: boolean;
  agent_id: string;
  agent_role: string;
  workbook_path: string | null;
  error: string | null;
  // Mirror of CompleteData.warnings for per-agent completion events.
  warnings?: string[];
  // Honest-completion flag: present (non-null) when the agent finalised the
  // statement via acknowledge_unresolved — the workbook is saved but a known
  // imbalance / unfilled-mandatory was accepted and needs human review.
  // success stays true (extraction DID finalise); this string is the reason.
  flag?: string | null;
}

/** Cross-check result as emitted in run_complete SSE event.
 *
 * ``"warning"`` is advisory (Phase 6.1 notes-consistency check). It
 * doesn't affect the overall run status but surfaces in the Validator
 * tab so operators can eyeball the disagreement.
 */
export interface CrossCheckResult {
  name: string;
  status: "passed" | "failed" | "warning" | "not_applicable" | "pending";
  expected: number | null;
  actual: number | null;
  diff: number | null;
  tolerance: number | null;
  message: string;
  // Review Workspace Step 8 — click-to-cell target (null when no anchor).
  target_sheet?: string | null;
  target_row?: number | null;
}

/** Final aggregate event for multi-agent runs. */
export interface RunCompleteData {
  success: boolean;
  // The single authoritative terminal status (UX-QA #22): completed |
  // completed_with_errors | correction_exhausted | failed | aborted. Passed
  // through so the live UI shows one honest label per outcome instead of a
  // binary success/"Didn't finish". Absent on legacy / early-reject events.
  overall_status?: string;
  // Present when the backend rejects a run before any agent starts
  // (unknown statement, invalid infopack, model setup failure, …). The
  // happy-path success event and per-agent-failure rollups don't carry it.
  message?: string;
  merged_workbook?: string | null;
  merge_errors?: string[];
  cross_checks?: CrossCheckResult[];
  cross_checks_partial?: boolean;
  statements_completed?: string[];
  statements_failed?: string[];
  // Honest-completion flag (peer-review F1): statements that finalised with an
  // acknowledged, audited gap. They are ALSO in statements_completed (the data
  // is saved); this array tells the UI to badge them "needs review".
  statements_flagged?: string[];
  // Notes-agent rollups. The backend emits these even when the notes
  // coordinator crashed before per-agent `complete` events landed — so
  // the reducer reconciles notes tabs from these arrays to avoid pending
  // skeletons sticking forever (peer-review #3). Template values match
  // NotesTemplateType enum (CORP_INFO, ACC_POLICIES, …).
  notes_completed?: string[];
  notes_failed?: string[];
  // Phase E (canonical mode): number of reconciliation conflicts still open
  // after the correction pass. >0 means the run needs human reconciliation in
  // the Concepts page. Absent / 0 in legacy mode.
  open_conflicts?: number;
  // Audit run id emitted on the aggregate completion event.
  run_id?: number;
}

export interface ToolTimelineEntry {
  tool_call_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  result_summary: string | null;   // Null until tool_result arrives
  duration_ms: number | null;
  startTime: number;
  endTime: number | null;
  phase: EventPhase | null;
  // Optional explicit lifecycle state. When unset, the card derives:
  //   result_summary === null  → "active"
  //   result_summary !== null  → "done"
  // Callers can pass "failed" or "cancelled" explicitly when they have
  // out-of-band signal (e.g. a future backend emits a tool-level error).
  state?: "active" | "done" | "failed" | "cancelled";
}

export interface ResultJsonData {
  fields: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

// --- Phase 8/9: Multi-agent run configuration types ---

export interface ModelEntry {
  id: string;
  display_name: string;
  provider: string;
  supports_vision: boolean;
  notes: string;
}

export type StatementType = "SOFP" | "SOPL" | "SOCI" | "SOCF" | "SOCIE";

export const STATEMENT_TYPES: StatementType[] = ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"];

/**
 * Build a `Record<StatementType, V>` by calling `fn` once per statement type.
 * Shared by `PreRunPanel`'s `makeEmptySelections` / `makeAllEnabled` style
 * initialisers so the iteration + typed-object scaffolding isn't re-implemented.
 */
export function mapStatements<V>(fn: (st: StatementType) => V): Record<StatementType, V> {
  const out = {} as Record<StatementType, V>;
  for (const stmt of STATEMENT_TYPES) {
    out[stmt] = fn(stmt);
  }
  return out;
}

export const STATEMENT_LABELS: Record<StatementType, string> = {
  SOFP: "Statement of Financial Position",
  SOPL: "Statement of Profit or Loss",
  SOCI: "Statement of Comprehensive Income",
  SOCF: "Statement of Cash Flows",
  SOCIE: "Statement of Changes in Equity",
};

/** Known variants per statement type (matches statement_types.py registry).
 *  NotPrepared is a meta-variant meaning no standalone SOCI was found —
 *  it's included so the UI can display it but extraction is skipped. */
export const VARIANTS: Record<StatementType, string[]> = {
  SOFP: ["CuNonCu", "OrderOfLiquidity"],
  SOPL: ["Function", "Nature"],
  SOCI: ["BeforeTax", "NetOfTax", "NotPrepared"],
  SOCF: ["Indirect", "Direct"],
  SOCIE: ["Default"],
};

export type ConfidenceLevel = "high" | "medium" | "low";

export interface VariantSelection {
  variant: string;
  confidence: ConfidenceLevel | null;  // null when manually selected
}

export interface ExtendedSettingsResponse extends SettingsResponse {
  available_models: ModelEntry[];
  default_models: Record<string, string>;
  scout_enabled_default: boolean;
  tolerance_rm: number;
  /** Whether the reviewer pass auto-runs after extraction (Settings toggle). */
  auto_review: boolean;
  /** Whether a clean run (no failed checks) still gets a spot-check (issue 1). */
  spot_check?: boolean;
  /** Spot-check depth: 'light' (default) | 'full'. */
  spot_check_mode?: string;
  /** Whether per-entity advisory memory injects prior-year prompt hints (item 28). */
  entity_memory?: boolean;
}

export type FilingLevel = "company" | "group";

/** Parallel to `FilingLevel` — which taxonomy the templates come from.
 *  Default is MFRS so every pre-existing caller keeps working. */
export type FilingStandard = "mfrs" | "mpers";

/** Scout's auto-detected standard from TOC / front-matter text. The UI
 *  preselects the toggle from this; the user toggle always wins. */
export type DetectedStandard = FilingStandard | "unknown";

/** Presentation denomination the user declares for the source statements.
 *  The agent transcribes figures verbatim and treats this as the authoritative
 *  scale instead of guessing it. Default "thousands" (RM '000). */
export type Denomination = "units" | "thousands" | "millions";

/** Human labels for the denomination toggle. */
export const DENOMINATION_LABELS: Record<Denomination, string> = {
  units: "RM",
  thousands: "RM '000",
  millions: "RM mil",
};

/** Registered variant names per (statement, filing_standard). Mirrors the
 *  backend `variants_for_standard` — SoRE is MPERS-only, everything else
 *  is available on both. NotPrepared is a meta-variant for SOCI only. */
export function variantsFor(
  statement: StatementType,
  standard: FilingStandard,
): string[] {
  if (statement === "SOCIE") {
    return standard === "mpers" ? ["Default", "SoRE"] : ["Default"];
  }
  return VARIANTS[statement];
}

/** Notes templates — mirror of NotesTemplateType in notes_types.py. */
export type NotesTemplateType =
  | "CORP_INFO"
  | "ACC_POLICIES"
  | "LIST_OF_NOTES"
  | "ISSUED_CAPITAL"
  | "RELATED_PARTY";

export const NOTES_TEMPLATE_TYPES: NotesTemplateType[] = [
  "CORP_INFO",
  "ACC_POLICIES",
  "LIST_OF_NOTES",
  "ISSUED_CAPITAL",
  "RELATED_PARTY",
];

export const NOTES_TEMPLATE_LABELS: Record<NotesTemplateType, string> = {
  CORP_INFO: "Corporate Information (Note 10)",
  ACC_POLICIES: "Accounting Policies (Note 11)",
  LIST_OF_NOTES: "List of Notes (Note 12)",
  ISSUED_CAPITAL: "Issued Capital (Note 13)",
  RELATED_PARTY: "Related Party Transactions (Note 14)",
};

/** Shape sent to POST /api/run/{session_id} */
export interface RunConfigPayload {
  statements: StatementType[];
  variants: Record<string, string>;
  /** Per-statement model overrides. */
  models?: Record<string, string>;
  infopack: Record<string, unknown> | null;
  use_scout: boolean;
  filing_level: FilingLevel;
  /** Filing standard — MFRS or MPERS. Defaults to MFRS server-side; the
   *  UI always sends it explicitly so history carries the toggle state. */
  filing_standard: FilingStandard;
  /** Presentation denomination the filer declares for the source figures.
   *  Defaults to "thousands" server-side; the UI always sends it so history
   *  carries the toggle state. */
  denomination: Denomination;
  notes_to_run?: NotesTemplateType[];
  /** Per-notes-template model overrides. Unspecified templates fall back
   *  to the run's default model on the backend. Sent only when the user
   *  explicitly selects notes templates; matches the face-statement
   *  ``models`` field shape for consistency. */
  notes_models?: Partial<Record<NotesTemplateType, string>>;
  /** Gold-standard eval (v16): the benchmark to grade this run against, or
   *  unset/null for a normal run. Set by the extract-page "Eval testing"
   *  toggle; persisted on runs.benchmark_id and graded at run completion. */
  benchmark_id?: number | null;
  /** Evals workspace (v30): how many identically-configured runs to launch
   *  back-to-back for a consistency measurement. 1 (default) = a single normal
   *  run; 2–5 links the runs into a repeat group. */
  repeats?: number;
}

// --- Phase 10: Per-agent state for tab-based UI ---

export type AgentTabStatus = "pending" | "running" | "complete" | "failed" | "cancelled" | "aborting";

/**
 * Per-agent streaming state — one per agent in a multi-agent run.
 *
 * Phase 6: the chat-view streaming fields (thinkingBuffer, activeThinkingId,
 * thinkingBlocks, streamingText, textSegments) were removed when the chat
 * feed was replaced by the tool-call timeline. We now only track the event
 * stream and the derived toolTimeline plus per-agent metadata.
 */
export interface AgentState {
  agentId: string;
  role: string;            // e.g. "SOFP", "SOPL", "scout", "validator"
  label: string;           // Display label
  status: AgentTabStatus;  // "pending" | "running" | "complete" | "failed" | "cancelled" | "aborting"
  currentPhase: EventPhase | null;
  events: SSEEvent[];
  toolTimeline: ToolTimelineEntry[];
  tokens: TokenData | null;
  error: ErrorData | null;
  workbookPath: string | null;
  // Honest-completion flag (peer-review F1): non-null when this agent
  // finalised with an acknowledged, audited gap. status stays "complete"
  // (the data is saved) but the UI badges it "needs review".
  flag?: string | null;
  // Phase 5.2 / peer-review [M1]: when the backend emits a Sheet-12
  // sub-agent `started` event it carries structured batch metadata.
  // We aggregate the ranges across all sub-agents so the Notes-12 tab
  // label can show "Notes 1-15, pp 18-37" at a glance without parsing
  // individual message strings. Empty arrays = no sub-agent metadata
  // seen yet (pre-start or non-fan-out sheets).
  subAgentBatchRanges?: Array<{
    subAgentId: string;
    notes: [number, number];   // inclusive note-number range for the batch
    pages: [number, number];   // inclusive PDF page range for the batch
  }>;
}

// ---------------------------------------------------------------------------
// Phase 5: History API wire shapes
//
// Matches the JSON emitted by `GET /api/runs`, `GET /api/runs/{id}`, etc.
// Kept as plain interfaces (not classes) so the fetch helpers can return
// decoded JSON directly without any conversion step.
// ---------------------------------------------------------------------------

export interface RunSummaryJson {
  id: number;
  created_at: string;
  pdf_filename: string;
  status: string;                 // 'running' | 'completed' | 'failed' | 'aborted'
  session_id: string;
  statements_run: string[];
  models_used: string[];
  duration_seconds: number | null;
  scout_enabled: boolean;
  has_merged_workbook: boolean;
  filing_level?: FilingLevel;
  filing_standard?: FilingStandard;
  denomination?: Denomination;
  // Gold-standard eval (v16): the benchmark this run graded against (null on
  // normal runs) + the headline accuracy in [0, 1] (null when not graded).
  // Powers the History score column + sparkline.
  benchmark_id?: number | null;
  eval_score?: number | null;
}

export interface RunListResponse {
  runs: RunSummaryJson[];
  total: number;
  limit: number;
  offset: number;
}

// v8 telemetry: per-agent token split + iteration counts.
export interface AgentTokenBreakdown {
  prompt_tokens: number;
  completion_tokens: number;
  turn_count: number;
  tool_call_count: number;
  // v15 cache telemetry. Optional so a pre-v15 payload still type-checks;
  // the panel defaults them to 0. cache_read_tokens > 0 == caching is hitting.
  cache_read_tokens?: number;
  cache_write_tokens?: number;
}

// v8 telemetry: one per-turn metrics row (a single agent.iter() node).
// Token figures are deltas vs the prior turn; cumulative_tokens is the
// running total after the turn. Content lives in the trace, not here.
export interface AgentTurnJson {
  turn_index: number;
  node_kind: string | null;        // 'model_request' | 'call_tools'
  tool_names: string | null;       // comma-joined; null for pure model turns
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cumulative_tokens: number;
  cost_estimate: number;
  duration_ms: number;
  // v15 cache telemetry deltas for this turn. Optional for back-compat.
  cache_read_tokens?: number;
  cache_write_tokens?: number;
}

export interface RunAgentJson {
  id: number;
  statement_type: string;
  variant: string | null;
  model: string | null;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  workbook_path: string | null;
  total_tokens: number | null;
  total_cost: number | null;
  // v17 (item 9): machine-readable failure class (turn_timeout,
  // iteration_capped, wallclock, token_budget_exceeded, projection_failed,
  // save_gate_refused, tool_exception, cancelled, no_write). Null on
  // success; optional so legacy payloads still type-check.
  error_type?: string | null;
  // v8 telemetry. Optional so a legacy detail payload (or an older backend)
  // still type-checks; the API client defaults them.
  token_breakdown?: AgentTokenBreakdown;
  turns?: AgentTurnJson[];
  // Phase 8: persisted SSE-equivalent events. The API client normalises
  // a missing field to [] so downstream consumers never have to null-check.
  events: SSEEvent[];
}

// v8 run-level rollup returned on the detail payload.
export interface TelemetryRollupJson {
  total_tokens: number;
  total_cost: number;
  prompt_tokens: number;
  completion_tokens: number;
  turn_count: number;
  tool_call_count: number;
  // v15 cache telemetry rollup. Optional for back-compat; defaulted to 0.
  cache_read_tokens?: number;
  cache_write_tokens?: number;
}

// v8 conversation trace served by GET /api/runs/{id}/agents/{stmt}/trace.
// `messages` is the verbatim pydantic-ai message list; `turns` mirrors the
// per-turn metrics so a viewer can line up tokens/timing with content.
export interface AgentTraceJson {
  messages: unknown[];
  turns?: AgentTurnJson[];
}

export interface RunCrossCheckJson {
  name: string;
  status: "passed" | "failed" | "not_applicable" | "pending";
  expected: number | null;
  actual: number | null;
  diff: number | null;
  tolerance: number | null;
  message: string;
  // Review Workspace Step 8 — click-to-cell target (null when no anchor).
  target_sheet?: string | null;
  target_row?: number | null;
}

export interface RunDetailJson {
  id: number;
  created_at: string;
  pdf_filename: string;
  status: string;
  session_id: string;
  output_dir: string;
  merged_workbook_path: string | null;
  scout_enabled: boolean;
  started_at: string | null;
  ended_at: string | null;
  config: Record<string, unknown> | null;
  filing_level?: FilingLevel;
  filing_standard?: FilingStandard;
  denomination?: Denomination;
  agents: RunAgentJson[];
  cross_checks: RunCrossCheckJson[];
  // v8 telemetry rollup. Optional for back-compat with older payloads.
  telemetry_rollup?: TelemetryRollupJson;
  // Gold-standard eval (v16): the benchmark this run graded against (null on
  // normal runs — the Eval tab is gated on it) + the scorecard (null when not
  // graded).
  benchmark_id?: number | null;
  eval_score?: EvalScoreJson | null;
  // v30 evals workspace: set when this run is one of N repeats launched
  // together for a consistency measurement (docs/PLAN-evals-workspace.md).
  repeat_group_id?: number | null;
  repeat_index?: number | null;
  app_version?: string | null;
}

// Evals workspace (v30): a repeat group + its computed consistency result,
// as returned by GET /api/repeat-groups/{id}. Feeds the ConsistencyPanel.
export interface RepeatGroupJson {
  id: number;
  created_at: string;
  repeats_requested: number;
  benchmark_id: number | null;
  status: string; // running | complete | partial
  config: Record<string, unknown> | null;
  consistency: ConsistencyJson | null;
  // accuracy: the repeat's own graded score (null when ungraded) — the PRD
  // requires per-repeat accuracy next to the stability figure (Step 11).
  runs: {
    id: number;
    status: string;
    repeat_index: number | null;
    accuracy?: number | null;
  }[];
}

export interface ConsistencyDisagreement {
  key: [string, string, string]; // concept_uuid, period, entity_scope
  // Human line-item name resolved server-side (Step 11); absent when the
  // concept uuid no longer resolves — the panel falls back to the raw key.
  sheet?: string;
  label?: string;
  // presence rows
  filled_by?: number[];
  n_present?: number;
  n_repeats?: number;
  // value rows
  values?: number[];
  spread?: number;
}

export interface ConsistencyJson {
  available: boolean;
  n_repeats: number;
  union_slots: number;
  unanimous: number;
  consistency: number | null;
  presence_disagreements: ConsistencyDisagreement[];
  value_disagreements: ConsistencyDisagreement[];
  unanimous_right: number | null;
  unanimous_wrong: number | null;
}

// --- Evals workspace: suites + batch runner + results (Phase E/F) ---

export interface SuiteSummaryJson {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
  doc_count: number;
  run_count: number;
}

export interface SuiteDocJson {
  id: number;
  label: string;
  source_filename: string;
  filing_standard: string;
  filing_level: string;
  benchmark_id: number | null;
  created_at: string;
}

export interface SuiteJson {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
  docs: SuiteDocJson[];
}

export interface SuiteRunLaunch {
  label?: string;
  model?: string | null;
  statements?: string[];
  variants?: Record<string, string>;
  use_scout?: boolean;
  notes_to_run?: string[];
  repeats?: number;
}

export interface SuiteRunSummaryJson {
  id: number;
  suite_id: number;
  label: string;
  model: string | null;
  app_version: string | null;
  status: string;
  created_at: string;
  ended_at: string | null;
}

export interface SuiteEstimateJson {
  documents: number;
  repeats: number;
  extraction_runs: number;
  avg_run_seconds: number | null;
  estimated_wall_seconds: number | null;
  concurrency: number;
}

export interface DocumentScorecardJson {
  run_id: number;
  label: string;
  status: string;
  failed: boolean;
  accuracy: number | null;
  gold_cells: number;
  matched_cells: number;
  taxonomy: Record<string, number>;
  per_statement: Record<string, { gold_cells: number; matched: number }>;
  consistency: number | null;
  cross_check_pass_rate: number | null;
  reviewer_flags: number;
  failed_agents: number;
  total_tokens: number;
  duration_s: number | null;
  notes_coverage: number | null;
  notes_coverage_available: boolean;
}

export interface SuiteAggregateJson {
  documents_total: number;
  documents_graded: number;
  documents_failed: number;
  coverage_note: string;
  mean_accuracy: number | null;
  pooled_accuracy: number | null;
  pooled_matched: number;
  pooled_gold: number;
  worst_document: DocumentScorecardJson | null;
  taxonomy_totals: Record<string, number>;
  mean_consistency: number | null;
  mean_notes_coverage: number | null;
  mean_cross_check_pass_rate: number | null;
}

export interface SuiteRunDetailJson {
  suite_run: SuiteRunSummaryJson & { config?: Record<string, unknown> | null };
  documents: DocumentScorecardJson[];
  aggregate: SuiteAggregateJson;
}

export interface SuiteResultPointJson {
  suite_run_id: number;
  label: string;
  model: string | null;
  app_version: string | null;
  created_at: string;
  status: string;
  mean_accuracy: number | null;
  mean_consistency: number | null;
  mean_cross_check_pass_rate: number | null;
}

export interface SuiteResultsJson {
  suite_id: number;
  points: SuiteResultPointJson[];
}

export interface SuiteCompareDocJson {
  doc_id: number;
  label: string;
  in_both: boolean;
  accuracy_a: number | null;
  accuracy_b: number | null;
  delta: number | null;
  gold_changed: boolean;
  // Drill-down linkage (Step 12): the representative run per side + the gold
  // benchmark, so a compare row can open its value-level slot diff.
  run_id_a?: number | null;
  run_id_b?: number | null;
  benchmark_id?: number | null;
}

export interface SuiteCompareJson {
  suite_run_a: number;
  suite_run_b: number;
  documents: SuiteCompareDocJson[];
  aggregate_delta: number | null;
  mean_accuracy_a: number | null;
  mean_accuracy_b: number | null;
  common_documents: number;
  only_in_one: number;
  taxonomy_delta: Record<string, number>;
  gold_changed_any: boolean;
}

// Gold-standard eval (v16) scorecard, as returned by GET /api/runs/{id}/eval
// and embedded in the run detail. `score` = matched / gold_cells in [0, 1].
export interface EvalScoreJson {
  benchmark_id: number;
  gold_cells: number;
  matched_cells: number;
  missing_cells: number;
  mismatch_cells: number;
  extra_cells: number;
  scale_mismatch: number;
  score: number;
  created_at?: string;
  // v30 (docs/PLAN-evals-workspace.md): failure diagnosis + per-statement
  // breakdown. Null on legacy scorecards graded before the taxonomy existed.
  taxonomy?: EvalTaxonomy | null;
  per_statement?: Record<string, { gold_cells: number; matched: number }> | null;
  // v33 gold-change guard: true = the reference answers were edited after
  // this score was stamped (re-grade offered); null = unknown (legacy row).
  gold_stale?: boolean | null;
}

// Reviewer contribution to a graded run (Step 12) — pre-reviewer snapshot
// score vs final score. available:false when no reviewer pass ran.
export interface ReviewerLiftJson {
  available: boolean;
  run_id: number;
  pre_matched?: number;
  final_matched?: number;
  lift_slots?: number;
  gold_cells?: number;
  pre_accuracy?: number | null;
  final_accuracy?: number | null;
}

// One slot in the compare drill-down (Step 12): the raw concept key plus the
// human name resolved server-side (absent when the uuid no longer resolves).
export interface SlotDiffRowJson {
  key: [string, string, string];
  gold: number;
  sheet?: string;
  label?: string;
}

export interface SlotDiffJson {
  doc_id: number;
  run_id_a: number;
  run_id_b: number;
  benchmark_id: number;
  regressions: SlotDiffRowJson[];
  fixes: SlotDiffRowJson[];
}

// Diagnosed failure counts — a partition of the wrong slots (missing +
// mismatch). Every key optional so a partial/legacy blob renders safely.
export interface EvalTaxonomy {
  period_swap?: number;
  scope_swap?: number;
  sign_flip?: number;
  scale?: number;
  plain_wrong?: number;
  false_not_disclosed?: number;
  misplaced?: number;
  unaddressed?: number;
}

// One benchmark in the library (GET /api/benchmarks list shape).
export interface BenchmarkJson {
  id: number;
  name: string;
  document: string | null;
  filing_standard: string;
  filing_level: string;
  created_at: string;
  statements: string[];
  gold_cell_count: number;
}

export interface RunsFilterParams {
  q?: string;
  status?: string;
  model?: string;
  standard?: FilingStandard;
  dateFrom?: string;
  dateTo?: string;
  /** Evals workspace (E6): include suite child runs (hidden by default). */
  includeSuiteChildren?: boolean;
  limit?: number;
  offset?: number;
}

export function createAgentState(agentId: string, role: string, label: string): AgentState {
  return {
    agentId,
    role,
    label,
    status: "pending",
    currentPhase: null,
    events: [],
    toolTimeline: [],
    tokens: null,
    error: null,
    workbookPath: null,
  };
}
