import type { EventPhase, PipelineStage, PreparationAction, PreparationPhase } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  currentPhase: EventPhase | null;
  pipelineStage?: PipelineStage | null;
  preparationPhase?: PreparationPhase | null;
  preparationActive?: boolean;
  preparationStopped?: boolean;
  preparationAction?: PreparationAction;
  isRunning: boolean;
  isComplete: boolean;
}

const PHASES = [
  { key: "prepare", label: "Prepare document" },
  { key: "confirm", label: "Confirm setup" },
  { key: "extract", label: "Extract" },
  { key: "check", label: "Check" },
  { key: "review", label: "Review" },
  { key: "ready", label: "Ready" },
];

function runStageIndex(stage: PipelineStage | null | undefined): number | null {
  if (!stage) return null;
  if (["scouting", "reading_source", "transcribing_source", "extracting"].includes(stage)) return 2;
  if (stage === "merging" || stage === "cross_checking") return 3;
  if (["correcting", "reviewing", "re_checking", "reviewing_notes", "formatting_notes", "validating_notes"].includes(stage)) return 4;
  if (stage === "done") return 5;
  return null;
}

function preparationStageIndex(phase: PreparationPhase | null | undefined): number | null {
  if (phase == null) return null;
  if (phase === "pending") return 0;
  if (["preparing_pages", "building_map", "reconciling_map"].includes(phase)) return 0;
  if (phase === "awaiting_confirmation") return 1;
  return null;
}

function agentPhaseIndex(phase: EventPhase | null): number {
  if (!phase) return -1;
  const indexes: Record<EventPhase, number> = {
    starting: 2,
    scouting: 2,
    started: 2,
    reading_template: 2,
    viewing_pdf: 2,
    writing_notes: 2,
    filling_workbook: 3,
    verifying: 4,
    cancelled: 4,
    complete: 5,
  };
  return indexes[phase];
}

type StepStatus = "completed" | "active" | "action" | "stopped" | "pending";

function getStepStatus(
  phaseIndex: number,
  currentIndex: number,
  isActive: boolean,
  isComplete: boolean,
  actionIndex: number | null,
  stoppedIndex: number | null,
): StepStatus {
  if (isComplete) return "completed";
  if (currentIndex < 0) return "pending";
  if (phaseIndex < currentIndex) return "completed";
  if (phaseIndex === stoppedIndex) return "stopped";
  if (phaseIndex === actionIndex) return "action";
  if (phaseIndex === currentIndex && isActive) return "active";
  return "pending";
}

const styles = {
  container: {
    overflowX: "auto" as const,
    paddingBottom: pwc.space.xs,
  },
  track: {
    display: "grid",
    gridTemplateColumns: "repeat(6, minmax(108px, 1fr))",
    minWidth: 680,
  } as React.CSSProperties,
  stepWrap: {
    minWidth: 0,
  } as React.CSSProperties,
  step: {
    display: "flex",
    alignItems: "center",
    width: "100%",
  },
  // Circle indicators
  completeCircle: {
    width: 18,
    height: 18,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: pwc.white,
    border: `2px solid ${pwc.success}`,
    flexShrink: 0,
  } as React.CSSProperties,
  completeDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: pwc.success,
  } as React.CSSProperties,
  activeCircle: {
    width: 18,
    height: 18,
    borderRadius: "50%",
    background: pwc.orange400,
    flexShrink: 0,
  } as React.CSSProperties,
  actionCircle: {
    width: 18,
    height: 18,
    borderRadius: "50%",
    border: `2px solid ${pwc.orange400}`,
    background: pwc.white,
    flexShrink: 0,
  } as React.CSSProperties,
  stoppedCircle: {
    width: 18,
    height: 18,
    borderRadius: "50%",
    border: `2px solid ${pwc.error}`,
    background: pwc.errorBg,
    flexShrink: 0,
  } as React.CSSProperties,
  pendingCircle: {
    width: 18,
    height: 18,
    borderRadius: "50%",
    border: `2px solid ${pwc.grey300}`,
    background: "transparent",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: pwc.grey300,
    fontSize: 10,
    fontFamily: pwc.fontMono,
    flexShrink: 0,
  } as React.CSSProperties,
  // Labels
  completedLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
    marginTop: pwc.space.sm,
  },
  activeLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.grey900,
    marginTop: pwc.space.sm,
  },
  actionLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.orange700,
    marginTop: pwc.space.sm,
  },
  stoppedLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.errorText,
    marginTop: pwc.space.sm,
  },
  pendingLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
    marginTop: pwc.space.sm,
  },
  // Connector
  connector: {
    height: 2,
    flex: 1,
    margin: `0 ${pwc.space.sm}px`,
  },
};

export function PipelineStages({
  currentPhase,
  pipelineStage,
  preparationPhase,
  preparationActive = false,
  preparationStopped = false,
  preparationAction = "none",
  isRunning,
  isComplete,
}: Props) {
  const preparationIndex = preparationStageIndex(preparationPhase);
  const currentIndex = preparationIndex ?? runStageIndex(pipelineStage) ?? agentPhaseIndex(currentPhase);
  const active = preparationIndex != null ? preparationActive : isRunning;
  const actionIndex = preparationAction === "confirm_setup" ? 1 : null;
  const stoppedIndex = preparationStopped ? preparationIndex : null;

  return (
    <div aria-label="Workflow progress" style={styles.container}>
      <div style={styles.track}>
        {PHASES.map((phase, i) => {
          const status = getStepStatus(i, currentIndex, active, isComplete, actionIndex, stoppedIndex);

          return (
            <div key={phase.key} style={styles.stepWrap}>
              <div style={styles.step}>
              {/* Step indicator */}
              {status === "completed" && (
                <div data-testid="step-complete" style={styles.completeCircle}>
                  <span style={styles.completeDot} />
                </div>
              )}
              {status === "active" && (
                <div data-testid="step-active" className="pwc-working-indicator" style={styles.activeCircle} />
              )}
              {status === "action" && (
                <div data-testid="step-action" style={styles.actionCircle} />
              )}
              {status === "stopped" && (
                <div data-testid="step-stopped" style={styles.stoppedCircle} />
              )}
              {status === "pending" && (
                <div data-testid="step-pending" style={styles.pendingCircle}>
                  {i + 1}
                </div>
              )}

                {/* Connector line between steps */}
                {i < PHASES.length - 1 && (
                  <div
                    data-testid="connector"
                    style={{
                      ...styles.connector,
                      background:
                        status === "completed" &&
                        getStepStatus(i + 1, currentIndex, active, isComplete, actionIndex, stoppedIndex) !== "pending"
                          ? pwc.success
                          : pwc.grey200,
                    }}
                  />
                )}
              </div>

              <div
                aria-current={status === "active" || status === "action" || status === "stopped" ? "step" : undefined}
                style={
                  status === "completed"
                    ? styles.completedLabel
                    : status === "active"
                      ? styles.activeLabel
                      : status === "action"
                        ? styles.actionLabel
                        : status === "stopped"
                          ? styles.stoppedLabel
                          : styles.pendingLabel
                }
              >
                {phase.label}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
