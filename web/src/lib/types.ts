export interface UploadResponse {
  session_id: string;
  filename: string;
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
  | "reading_template"
  | "viewing_pdf"
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
  | "run_complete";    // Final aggregate event for multi-agent runs

export interface SSEEvent {
  event: SSEEventType;
  data:
    | StatusData
    | ThinkingDeltaData
    | ThinkingEndData
    | TextDeltaData
    | ToolCallData
    | ToolResultData
    | TokenData
    | ErrorData
    | CompleteData
    | AgentCompleteData
    | RunCompleteData;
  timestamp: number;
}

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

export interface CompleteData {
  success: boolean;
  output_path: string;
  excel_path: string;
  trace_path: string;
  total_tokens: number;
  cost: number;
  statementsCompleted?: string[];
}

/** Per-agent completion event emitted by multi-agent runs. */
export interface AgentCompleteData {
  success: boolean;
  agent_id: string;
  agent_role: string;
  workbook_path: string | null;
  error: string | null;
}

/** Cross-check result as emitted in run_complete SSE event. */
export interface CrossCheckResult {
  name: string;
  status: "passed" | "failed" | "not_applicable" | "pending";
  expected: number | null;
  actual: number | null;
  diff: number | null;
  tolerance: number | null;
  message: string;
}

/** Final aggregate event for multi-agent runs. */
export interface RunCompleteData {
  success: boolean;
  merged_workbook: string | null;
  merge_errors: string[];
  cross_checks: CrossCheckResult[];
  statements_completed: string[];
  statements_failed: string[];
}

// --- P0: Extended state types for streaming ---

export interface ThinkingBlock {
  id: string;
  content: string;
  summary: string;
  timestamp: number;
  phase: EventPhase | null;        // Which pipeline phase this was in
  durationMs: number | null;       // Real reasoning duration from server (null if unknown)
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

/** Shape sent to POST /api/run/{session_id} */
export interface RunConfigPayload {
  statements: StatementType[];
  variants: Record<string, string>;
  models: Record<string, string>;
  infopack: Record<string, unknown> | null;
  use_scout: boolean;
}

// --- Phase 10: Per-agent state for tab-based UI ---

export type AgentTabStatus = "pending" | "running" | "complete" | "failed" | "cancelled" | "aborting";

/** Per-agent streaming state — one per agent in a multi-agent run. */
export interface AgentState {
  agentId: string;
  role: string;            // e.g. "SOFP", "SOPL", "scout", "validator"
  label: string;           // Display label
  status: AgentTabStatus;  // "pending" | "running" | "complete" | "failed" | "cancelled" | "aborting"
  currentPhase: EventPhase | null;
  events: SSEEvent[];
  thinkingBuffer: string;
  activeThinkingId: string | null;
  thinkingBlocks: ThinkingBlock[];
  toolTimeline: ToolTimelineEntry[];
  streamingText: string;
  tokens: TokenData | null;
  error: ErrorData | null;
  workbookPath: string | null;
}

export function createAgentState(agentId: string, role: string, label: string): AgentState {
  return {
    agentId,
    role,
    label,
    status: "pending",
    currentPhase: null,
    events: [],
    thinkingBuffer: "",
    activeThinkingId: null,
    thinkingBlocks: [],
    toolTimeline: [],
    streamingText: "",
    tokens: null,
    error: null,
    workbookPath: null,
  };
}
