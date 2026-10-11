import type { RunSummaryJson } from "./types";
import { runStatusDisplay, STATUS_SYMBOLS, type StatusSymbol } from "./runStatus";

/** Status family for a document's current stage — drives the one icon the
 *  sidebar shows beside a recent document instead of a second line of text. */
export function documentStageSymbol(run: Pick<RunSummaryJson, "status" | "preparation">): StatusSymbol {
  if (run.status === "draft") {
    const prep = run.preparation;
    if (prep?.status === "failed") return STATUS_SYMBOLS.failure;
    if (prep?.status === "cancelled") return STATUS_SYMBOLS.inactive;
    if (prep?.phase === "awaiting_confirmation") return STATUS_SYMBOLS.attention;
    if (prep?.status === "working" || prep?.status === "retrying" || prep?.status === "queued") return STATUS_SYMBOLS.inProgress;
    return STATUS_SYMBOLS.inactive;
  }
  if (run.status === "running") return STATUS_SYMBOLS.inProgress;
  return runStatusDisplay(run.status).symbol;
}

export function documentStageLabel(run: Pick<RunSummaryJson, "status" | "preparation" | "pipeline_stage">): string {
  if (run.status === "draft") {
    const prep = run.preparation;
    if (prep?.status === "failed") return "Preparation failed";
    if (prep?.status === "cancelled") return "Preparation stopped";
    if (prep?.phase === "awaiting_confirmation") return "Confirm setup";
    if (prep?.phase === "building_map") return "Mapping document";
    if (prep?.phase === "reconciling_map") return "Checking document map";
    if (prep?.status === "queued") return "Waiting to prepare";
    if (prep?.status === "working" || prep?.status === "retrying") return "Preparing document";
    return "Prepare document";
  }
  const names: Record<string, string> = {
    scouting: "Mapping document", reading_source: "Reading document", extracting: "Extracting",
    merging: "Combining statements", cross_checking: "Checking figures", reviewing: "Reviewing figures",
    correcting: "Reviewing figures", re_checking: "Checking corrections", reviewing_notes: "Reviewing notes",
    validating_notes: "Reviewing notes",
    formatting_notes: "Formatting notes", cleaning_notes: "Cleaning notes", saving: "Saving results",
    done: "Saving results",
  };
  return names[run.pipeline_stage || ""] || "Extraction in progress";
}
