// Single source of truth for the "non-agent" tab kinds — tabs whose
// lifecycle is independent of the per-agent run/stop/rerun flow:
//   - scout has its own Auto-detect / Stop controls in the pre-run panel
//   - validator is the synthetic cross-check phase tab
//   - NOTES_VALIDATOR is a real pseudo-agent that runs after merge but is
//     NOT tied to a user-picked statement/notes role (peer-review F1 —
//     before this, NOTES_VALIDATOR fell through to the statementsInRun
//     gate and got filtered out of the live extract view, which defeated
//     the notes-validator short-circuit "skipped" SSE emit we now ship).
//
// Used by AgentTabs (for tab-ordering buckets — these always render at
// the right edge / inside the notes-and-special bucket regardless of
// run selection) and by ActiveTabPanel (to hide the per-agent Stop /
// Rerun toolbar buttons when one of these tabs is active). Keeping the
// set in one module prevents the three callsites from drifting.

export const NON_AGENT_TAB_IDS: ReadonlySet<string> = new Set([
  "scout",
  "validator",
  "NOTES_VALIDATOR",
]);

export function isNonAgentTab(id: string): boolean {
  return NON_AGENT_TAB_IDS.has(id);
}
