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
  | "reading_template"
  | "viewing_pdf"
  | "filling_workbook"
  | "verifying"
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
  | "complete";        // Run finished

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
    | CompleteData;
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
