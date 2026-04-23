// Single source of truth for the "non-agent" tab kinds — tabs whose
// lifecycle is independent of the per-agent run/stop/rerun flow:
//   - scout has its own Auto-detect / Stop controls in the pre-run panel
//   - validator is a cross-check phase, not a runnable agent
//
// Used by AgentTabs (for tab-ordering buckets — scout/validator always
// render at the right edge) and by ActiveTabPanel (to hide the per-agent
// Stop / Rerun toolbar buttons when one of these tabs is active). Keeping
// the set in one module prevents the two callsites from drifting.

export const NON_AGENT_TAB_IDS: ReadonlySet<string> = new Set(["scout", "validator"]);

export function isNonAgentTab(id: string): boolean {
  return NON_AGENT_TAB_IDS.has(id);
}
