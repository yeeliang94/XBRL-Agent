// Shared Notes-12 identity constants.
//
// Sheet 12 has two different names depending on context:
//   - Live SSE: `agent_id` uses the `notes:<TEMPLATE>` namespace stamped
//     by the coordinator in notes/coordinator.py.
//   - Persisted DB / history: `statement_type` uses the `NOTES_<TEMPLATE>`
//     form written by the recorder in db/recorder.py.
//
// Both are correct for their context, but the gate logic in ExtractPage
// (live) and RunDetailView (replay) needs to test the right one. Keeping
// the pair together here so the next person who renames the sheet has a
// single place to update.

export const NOTES_12_AGENT_ID = "notes:LIST_OF_NOTES";
export const NOTES_12_STATEMENT_TYPE = "NOTES_LIST_OF_NOTES";

/** True if the given live agent_id is Sheet 12. */
export function isNotes12AgentId(agentId: string | null | undefined): boolean {
  return agentId === NOTES_12_AGENT_ID;
}

/** True if the given persisted DB statement_type is Sheet 12. */
export function isNotes12StatementType(statementType: string | null | undefined): boolean {
  return statementType === NOTES_12_STATEMENT_TYPE;
}
