import { pwc } from "./theme";
import { humanize } from "./vocabulary";

// ---------------------------------------------------------------------------
// Shared status normalization for History list / detail / filters.
//
// Why this exists:
//   - The server emits run-level status as one of:
//       running | completed | completed_with_errors | failed | aborted
//     (`server.py:870-880`).
//   - The coordinator persists per-agent status as one of:
//       running | succeeded | failed | cancelled
//     (`coordinator.py:74,429`).
//
// This module is the single source of truth for status → label/symbol
// mapping. Any new status added on the backend should be added here too.
//
// Design-system Status: routine status is MONOCHROME — a neutral symbol plus
// explicit text, never a coloured dot, pill, border, or fill. The canonical
// symbol families are:
//   ○  in progress
//   ✓  successful / verified / completed / extracted
//   !  action required / needs review / no source
//   ×  failed / aborted
//   –  draft / not started / skipped / unavailable / not applicable
//   ◇  calculated / derived
// Add a symbol only for a genuinely different user-facing concept, not every
// backend enum — the explicit label carries the precise state.
// ---------------------------------------------------------------------------

export const STATUS_SYMBOLS = {
  inProgress: "○",
  success: "✓",
  attention: "!",
  failure: "×",
  inactive: "–",
  derived: "◇",
} as const;

export type StatusSymbol = (typeof STATUS_SYMBOLS)[keyof typeof STATUS_SYMBOLS];

export interface RunStatusDisplay {
  label: string;
  /** Canonical neutral symbol (aria-hidden in the UI; the label is the
   *  accessible name). */
  symbol: StatusSymbol;
  // `accent` is the bright status hue retained for the EXCEPTIONAL surfaces
  // that still colour an accent (alerts, attention rules, charts). Routine
  // status rendering must not use it. `color`/`bg` are the legacy soft-fill
  // tokens, kept for the rare emphasis surfaces that tint a background.
  accent: string;
  color: string;
  bg: string;
}

const FALLBACK: RunStatusDisplay = {
  label: "Unknown",
  symbol: STATUS_SYMBOLS.inactive,
  accent: pwc.grey500,
  color: pwc.grey700,
  bg: pwc.grey100,
};

const RUN_STATUS_MAP: Record<string, RunStatusDisplay> = {
  // PLAN-persistent-draft-uploads.md (Phase D): drafts are unstarted
  // uploads — "waiting" rather than success/failure.
  draft:                  { label: "Not started",           symbol: STATUS_SYMBOLS.inactive,   accent: pwc.grey500,   color: pwc.grey700,     bg: pwc.grey100 },
  running:                { label: "Running",               symbol: STATUS_SYMBOLS.inProgress, accent: pwc.orange500, color: pwc.orange500,   bg: pwc.orange50 },
  completed:              { label: "Completed",             symbol: STATUS_SYMBOLS.success,    accent: pwc.success,   color: pwc.success,     bg: pwc.successBg },
  completed_with_errors:  { label: "Completed with errors", symbol: STATUS_SYMBOLS.attention,  accent: pwc.warning,   color: pwc.warningText, bg: pwc.warningBg },
  // RUN-REVIEW P0-1 (2026-04-26): correction agent hit its turn budget
  // without converging — needs a human. The distinct label keeps it apart
  // from a hard failure.
  correction_exhausted:   { label: "Needs review",          symbol: STATUS_SYMBOLS.attention,  accent: pwc.error,     color: pwc.errorText,   bg: pwc.warningBg },
  failed:                 { label: "Failed",                symbol: STATUS_SYMBOLS.failure,    accent: pwc.error,     color: pwc.error,       bg: pwc.errorBg },
  aborted:                { label: "Aborted",               symbol: STATUS_SYMBOLS.failure,    accent: pwc.grey500,   color: pwc.grey700,     bg: pwc.grey100 },
};

const AGENT_STATUS_MAP: Record<string, RunStatusDisplay> = {
  running:    { label: "Running",   symbol: STATUS_SYMBOLS.inProgress, accent: pwc.orange500, color: pwc.orange500, bg: pwc.orange50 },
  // Coordinator-emitted "succeeded" is the per-agent equivalent of run-level
  // "completed" — render as Completed so the UI uses one consistent verb.
  succeeded:  { label: "Completed", symbol: STATUS_SYMBOLS.success,    accent: pwc.success,   color: pwc.success,   bg: pwc.successBg },
  completed:  { label: "Completed", symbol: STATUS_SYMBOLS.success,    accent: pwc.success,   color: pwc.success,   bg: pwc.successBg },
  failed:     { label: "Failed",    symbol: STATUS_SYMBOLS.failure,    accent: pwc.error,     color: pwc.error,     bg: pwc.errorBg },
  cancelled:  { label: "Cancelled", symbol: STATUS_SYMBOLS.failure,    accent: pwc.grey500,   color: pwc.grey700,   bg: pwc.grey100 },
  aborted:    { label: "Aborted",   symbol: STATUS_SYMBOLS.failure,    accent: pwc.grey500,   color: pwc.grey700,   bg: pwc.grey100 },
};

/** Look up a run-level status. Unknown values get a clearly-labeled fallback
 *  rather than the raw enum string so users never see internal jargon. */
export function runStatusDisplay(status: string): RunStatusDisplay {
  // Unmapped values are humanised (underscores → spaces, sentence case) so a
  // future backend status never reaches the badge as a raw enum.
  return RUN_STATUS_MAP[status] ?? { ...FALLBACK, label: status ? humanize(status) : "Unknown" };
}

/** Look up a per-agent status. Falls through to runStatusDisplay so any
 *  status added on the run-level map is automatically supported here too. */
export function agentStatusDisplay(status: string): RunStatusDisplay {
  return (
    AGENT_STATUS_MAP[status] ??
    RUN_STATUS_MAP[status] ??
    { ...FALLBACK, label: status ? humanize(status) : "Unknown" }
  );
}

/** The set of run-status values the History filter dropdown surfaces. The
 *  order here is the order shown in the UI. Keep this list in sync with
 *  RUN_STATUS_MAP — any addition to the map should be considered for the
 *  filter list. `running` is intentionally last since it's transient.
 *  `draft` slots in between completed/failed and the transient running so
 *  users browsing for unstarted uploads find them quickly. */
export const RUN_STATUS_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: "completed", label: "Completed" },
  { value: "completed_with_errors", label: "Completed with errors" },
  { value: "correction_exhausted", label: "Needs review" },
  { value: "failed", label: "Failed" },
  { value: "aborted", label: "Aborted" },
  { value: "draft", label: "Not started" },
  { value: "running", label: "Running" },
];
