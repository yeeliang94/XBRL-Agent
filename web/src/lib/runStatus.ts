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
// The frontend's old hard-coded color maps only knew completed|running|
// failed|aborted, so any non-matching status fell through to a muted grey
// "raw enum" badge — visually inconsistent and confusing for users.
//
// This module is the single source of truth for status → label/color
// mapping. Any new status added on the backend should be added here too.
// ---------------------------------------------------------------------------

export interface RunStatusDisplay {
  label: string;
  // `accent` is the bright status hue used for the outline badge's border +
  // dot (design-system Badges: status is an accent, not a fill). `color`/`bg`
  // are the legacy soft-fill tokens, kept for the rare emphasis surfaces that
  // still tint a background (not used by the status badges anymore).
  accent: string;
  color: string;
  bg: string;
}

const FALLBACK: RunStatusDisplay = {
  label: "Unknown",
  accent: pwc.grey500,
  color: pwc.grey700,
  bg: pwc.grey100,
};

const RUN_STATUS_MAP: Record<string, RunStatusDisplay> = {
  // PLAN-persistent-draft-uploads.md (Phase D): drafts are unstarted
  // uploads. Neutral-grey so they read as "waiting" rather than
  // success/failure — distinct from `aborted` which shares the neutral dot.
  draft:                  { label: "Not started",          accent: pwc.grey500,  color: pwc.grey700,   bg: pwc.grey100 },
  running:                { label: "Running",              accent: pwc.orange500, color: pwc.orange500, bg: pwc.orange50 },
  completed:              { label: "Completed",            accent: pwc.success,   color: pwc.success,   bg: pwc.successBg },
  completed_with_errors:  { label: "Completed with errors", accent: pwc.warning,  color: pwc.warningText, bg: pwc.warningBg },
  // RUN-REVIEW P0-1 (2026-04-26): correction agent hit its turn budget
  // without converging. Carries the red (error) accent so the "needs a human"
  // runs read as more urgent than amber completed_with_errors; the distinct
  // label ("Needs review" vs "Failed") keeps it apart from a hard failure.
  correction_exhausted:   { label: "Needs review",         accent: pwc.error,     color: pwc.errorText,   bg: pwc.warningBg },
  failed:                 { label: "Failed",               accent: pwc.error,     color: pwc.error,     bg: pwc.errorBg },
  aborted:                { label: "Aborted",              accent: pwc.grey500,   color: pwc.grey700,   bg: pwc.grey100 },
};

const AGENT_STATUS_MAP: Record<string, RunStatusDisplay> = {
  running:    { label: "Running",   accent: pwc.orange500, color: pwc.orange500, bg: pwc.orange50 },
  // Coordinator-emitted "succeeded" is the per-agent equivalent of run-level
  // "completed" — render as Completed so the UI uses one consistent verb.
  succeeded:  { label: "Completed", accent: pwc.success,   color: pwc.success,   bg: pwc.successBg },
  completed:  { label: "Completed", accent: pwc.success,   color: pwc.success,   bg: pwc.successBg },
  failed:     { label: "Failed",    accent: pwc.error,     color: pwc.error,     bg: pwc.errorBg },
  cancelled:  { label: "Cancelled", accent: pwc.grey500,   color: pwc.grey700,   bg: pwc.grey100 },
  aborted:    { label: "Aborted",   accent: pwc.grey500,   color: pwc.grey700,   bg: pwc.grey100 },
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
