import { pwc } from "./theme";

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
// The frontend's old hard-coded color maps only knew completed|running|
// failed|aborted, so any non-matching status fell through to a muted grey
// "raw enum" badge — visually inconsistent and confusing for users.
//
// This module is the single source of truth for status → label/color
// mapping. Any new status added on the backend should be added here too.
// ---------------------------------------------------------------------------

export interface RunStatusDisplay {
  label: string;
  color: string;
  bg: string;
}

const FALLBACK: RunStatusDisplay = {
  label: "Unknown",
  color: pwc.grey700,
  bg: pwc.grey100,
};

const RUN_STATUS_MAP: Record<string, RunStatusDisplay> = {
  // PLAN-persistent-draft-uploads.md (Phase D): drafts are unstarted
  // uploads. Slate-grey neutral palette so they read as "waiting" rather
  // than success/failure — distinct from `aborted` which uses warmer grey.
  draft:                  { label: "Not started",          color: pwc.grey700,   bg: pwc.grey100 },
  running:                { label: "Running",              color: pwc.orange500, bg: pwc.orange50 },
  completed:              { label: "Completed",            color: pwc.success,   bg: pwc.successBg },
  completed_with_errors:  { label: "Completed with errors", color: "#D97706",    bg: "#FFFBEB" },
  // RUN-REVIEW P0-1 (2026-04-26): correction agent hit its turn budget
  // without converging. Distinct palette from completed_with_errors so
  // operators can spot the "needs human review" runs in History at a
  // glance — rendered with a more attention-grabbing amber+rose pair.
  correction_exhausted:   { label: "Needs review",         color: "#B45309",    bg: "#FEF3C7" },
  failed:                 { label: "Failed",               color: pwc.error,     bg: pwc.errorBg },
  aborted:                { label: "Aborted",              color: pwc.grey700,   bg: pwc.grey100 },
};

const AGENT_STATUS_MAP: Record<string, RunStatusDisplay> = {
  running:    { label: "Running",   color: pwc.orange500, bg: pwc.orange50 },
  // Coordinator-emitted "succeeded" is the per-agent equivalent of run-level
  // "completed" — render as Completed so the UI uses one consistent verb.
  succeeded:  { label: "Completed", color: pwc.success,   bg: pwc.successBg },
  completed:  { label: "Completed", color: pwc.success,   bg: pwc.successBg },
  failed:     { label: "Failed",    color: pwc.error,     bg: pwc.errorBg },
  cancelled:  { label: "Cancelled", color: pwc.grey700,   bg: pwc.grey100 },
  aborted:    { label: "Aborted",   color: pwc.grey700,   bg: pwc.grey100 },
};

/** Look up a run-level status. Unknown values get a clearly-labeled fallback
 *  rather than the raw enum string so users never see internal jargon. */
export function runStatusDisplay(status: string): RunStatusDisplay {
  return RUN_STATUS_MAP[status] ?? { ...FALLBACK, label: status || "Unknown" };
}

/** Look up a per-agent status. Falls through to runStatusDisplay so any
 *  status added on the run-level map is automatically supported here too. */
export function agentStatusDisplay(status: string): RunStatusDisplay {
  return (
    AGENT_STATUS_MAP[status] ??
    RUN_STATUS_MAP[status] ??
    { ...FALLBACK, label: status || "Unknown" }
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
