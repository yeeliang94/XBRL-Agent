import type { AppState } from "./appReducer";
import type { AgentTabStatus, PipelineStageData } from "./types";

type FormattingProgress = Pick<PipelineStageData, "completed" | "total" | "message">;

/** The overall run finishing does not prove that its formatter succeeded. */
export function notesFormattingActivity(
  state: Pick<AppState, "events" | "pipelineStage" | "pipelineActivity" | "isRunning">,
): { status: AgentTabStatus; message: string; progress: FormattingProgress | null } | null {
  let progress: FormattingProgress | null = null;
  let failure: string | null = null;
  for (const event of state.events) {
    if (event.event === "pipeline_stage" && event.data.stage === "formatting_notes") {
      progress = event.data;
    } else if (event.event === "error" && event.data.type === "notes_formatting_incomplete") {
      failure = event.data.message;
    }
  }
  if (state.pipelineStage === "formatting_notes") {
    progress = state.pipelineActivity ?? progress ?? {};
  }
  if (failure) return { status: "failed", message: failure, progress };
  if (!progress) return null;
  if (state.pipelineStage === "done") {
    return { status: "complete", message: "Formatting complete", progress };
  }
  if (!state.isRunning) {
    return { status: "cancelled", message: "Formatting stopped before completion. Review the saved notes.", progress };
  }
  return { status: "running", message: progress.message ?? "Applying MBRS formatting", progress };
}
