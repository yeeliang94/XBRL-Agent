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
  | "pipeline_stage";

// Every multi-agent event carries these routing fields inside `data` (the
// backend stamps them in coordinator._build_event). We keep them in `data`
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

export interface ErrorData {
  message: string;
  traceback: string;
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
  | "re_checking"
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

export interface CompleteData {
  success: boolean;
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
}

/** Final aggregate event for multi-agent runs. */
export interface RunCompleteData {
  success: boolean;
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
  // Notes-agent rollups. The backend emits these even when the notes
  // coordinator crashed before per-agent `complete` events landed — so
  // the reducer reconciles notes tabs from these arrays to avoid pending
  // skeletons sticking forever (peer-review #3). Template values match
  // NotesTemplateType enum (CORP_INFO, ACC_POLICIES, …).
  notes_completed?: string[];
  notes_failed?: string[];
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
}

export type FilingLevel = "company" | "group";

/** Parallel to `FilingLevel` — which taxonomy the templates come from.
 *  Default is MFRS so every pre-existing caller keeps working. */
export type FilingStandard = "mfrs" | "mpers";

/** Scout's auto-detected standard from TOC / front-matter text. The UI
 *  preselects the toggle from this; the user toggle always wins. */
export type DetectedStandard = FilingStandard | "unknown";

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
  models: Record<string, string>;
  infopack: Record<string, unknown> | null;
  use_scout: boolean;
  filing_level: FilingLevel;
  /** Filing standard — MFRS or MPERS. Defaults to MFRS server-side; the
   *  UI always sends it explicitly so history carries the toggle state. */
  filing_standard: FilingStandard;
  notes_to_run?: NotesTemplateType[];
  /** Per-notes-template model overrides. Unspecified templates fall back
   *  to the run's default model on the backend. Sent only when the user
   *  explicitly selects notes templates; matches the face-statement
   *  ``models`` field shape for consistency. */
  notes_models?: Partial<Record<NotesTemplateType, string>>;
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
}

export interface RunListResponse {
  runs: RunSummaryJson[];
  total: number;
  limit: number;
  offset: number;
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
  // Phase 8: persisted SSE-equivalent events. The API client normalises
  // a missing field to [] so downstream consumers never have to null-check.
  events: SSEEvent[];
}

export interface RunCrossCheckJson {
  name: string;
  status: "passed" | "failed" | "not_applicable" | "pending";
  expected: number | null;
  actual: number | null;
  diff: number | null;
  tolerance: number | null;
  message: string;
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
  agents: RunAgentJson[];
  cross_checks: RunCrossCheckJson[];
}

export interface RunsFilterParams {
  q?: string;
  status?: string;
  model?: string;
  standard?: FilingStandard;
  dateFrom?: string;
  dateTo?: string;
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
