import type { AppState } from "./appReducer";
import type { AgentTabStatus, PipelineStageData } from "./types";

type FormattingProgress = Pick<PipelineStageData, "completed" | "total" | "message" | "status">;

/** The overall run finishing does not prove that its formatter succeeded. */
export function notesFormattingActivity(
  state: Pick<AppState, "events" | "pipelineStage" | "pipelineActivity" | "isRunning">,
): { status: AgentTabStatus; message: string; progress: FormattingProgress | null } | null {
  let progress: FormattingProgress | null = null;
  let failure: string | null = null;
  for (const event of state.events) {
    if (event.event === "pipeline_stage" && event.data.stage === "formatting_notes") {
      progress = { ...(progress ?? {}), ...event.data };
    } else if (event.event === "error" && event.data.type === "notes_formatting_incomplete") {
      failure = event.data.message;
    }
  }
  if (state.pipelineStage === "formatting_notes") {
    progress = { ...progress, ...state.pipelineActivity };
  }
  if (failure) return { status: "failed", message: failure, progress };
  if (!progress) return null;
  if (progress.status === "failed") return { status: "failed", message: progress.message ?? "Notes appearance checking failed", progress };
  if (progress.status === "succeeded") return { status: "complete", message: progress.message ?? "Notes appearance verified", progress };
  if (state.pipelineStage === "done" || state.pipelineStage === "cleaning_notes") {
    return { status: "complete", message: "Formatting complete", progress };
  }
  if (!state.isRunning) {
    return { status: "cancelled", message: "Formatting stopped before completion. Review the saved notes.", progress };
  }
  return { status: "running", message: progress.message ?? "Applying MBRS formatting", progress };
}

/** A completed run never conceals an incomplete content cleanup. */
export function notesCleanupActivity(
  state: Pick<AppState, "events" | "pipelineStage" | "pipelineActivity" | "isRunning">,
): { status: AgentTabStatus; message: string; progress: FormattingProgress | null } | null {
  let progress: FormattingProgress | null = null;
  let failure: string | null = null;
  for (const event of state.events) {
    if (event.event === "pipeline_stage" && event.data.stage === "cleaning_notes") progress = { ...(progress ?? {}), ...event.data };
    if (event.event === "error" && event.data.type === "notes_cleanup_incomplete") failure = event.data.message;
  }
  if (state.pipelineStage === "cleaning_notes") progress = { ...progress, ...state.pipelineActivity };
  if (failure) return { status: "failed", message: failure, progress };
  if (!progress) return null;
  if (progress.status === "failed") return { status: "failed", message: progress.message ?? "Note content finalization failed", progress };
  if (progress.status === "succeeded") return { status: "complete", message: progress.message ?? "Note content finalized", progress };
  if (progress.total != null && progress.completed === progress.total) {
    return { status: "complete", message: progress.message ?? "Notes cleanup complete", progress };
  }
  if (!state.isRunning) return { status: "cancelled", message: "Notes cleanup stopped. Review the saved notes.", progress };
  return { status: "running", message: progress.message ?? "Checking notes for page banners and repeated headings", progress };
}

/** Completeness is assessed after content cleanup and repair, before styling. */
export function notesIntegrityActivity(
  state: Pick<AppState, "events" | "pipelineStage" | "pipelineActivity" | "isRunning">,
): ReturnType<typeof notesFormattingActivity> {
  let progress: FormattingProgress | null = null;
  for (const event of state.events) {
    if (event.event === "pipeline_stage" && event.data.stage === "checking_notes") {
      progress = { ...(progress ?? {}), ...event.data };
    }
  }
  if (state.pipelineStage === "checking_notes") progress = { ...(progress ?? {}), ...state.pipelineActivity };
  if (!progress) return null;
  const status = progress.status === "succeeded" ? "complete"
    : progress.status === "failed" ? "failed"
    : progress.status === "skipped" ? "skipped"
    : state.isRunning ? "running" : "cancelled";
  return { status, message: progress.message ?? "Checking notes completeness", progress };
}
