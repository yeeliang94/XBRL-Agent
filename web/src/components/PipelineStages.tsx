import type { EventPhase, PipelineStage } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  currentPhase: EventPhase | null;
  pipelineStage?: PipelineStage | null;
  isRunning: boolean;
  isComplete: boolean;
}

const PHASES = [
  { key: "prepare", label: "Prepare document" },
  { key: "extract", label: "Extract data" },
  { key: "check", label: "Combine & check" },
  { key: "review", label: "Review issues" },
  { key: "ready", label: "Ready" },
];

function runStageIndex(stage: PipelineStage | null | undefined): number | null {
  if (!stage) return null;
  if (["scouting", "reading_source", "transcribing_source"].includes(stage)) return 0;
  if (stage === "extracting") return 1;
  if (stage === "merging" || stage === "cross_checking") return 2;
  if (["correcting", "reviewing", "re_checking", "reviewing_notes", "formatting_notes", "validating_notes"].includes(stage)) return 3;
  if (stage === "done") return 4;
  return null;
}

function agentPhaseIndex(phase: EventPhase | null): number {
  if (!phase) return -1;
  const indexes: Record<EventPhase, number> = {
    starting: 0,
    scouting: 0,
    started: 0,
    reading_template: 0,
    viewing_pdf: 1,
    writing_notes: 2,
    filling_workbook: 2,
    verifying: 3,
    cancelled: 3,
    complete: 4,
  };
  return indexes[phase];
}

type StepStatus = "completed" | "active" | "pending";

function getStepStatus(
  phaseIndex: number,
  currentIndex: number,
  isRunning: boolean,
  isComplete: boolean,
): StepStatus {
  if (isComplete) return "completed";
  if (currentIndex < 0) return "pending";
  if (phaseIndex < currentIndex) return "completed";
  if (phaseIndex === currentIndex && isRunning) return "active";
  return "pending";
}

const styles = {
  container: {
    overflowX: "auto" as const,
    paddingBottom: pwc.space.xs,
  },
  track: {
    display: "grid",
    gridTemplateColumns: "repeat(5, minmax(116px, 1fr))",
    minWidth: 640,
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

export function PipelineStages({ currentPhase, pipelineStage, isRunning, isComplete }: Props) {
  const currentIndex = runStageIndex(pipelineStage) ?? agentPhaseIndex(currentPhase);

  return (
    <div aria-label="Extraction progress" style={styles.container}>
      <div style={styles.track}>
        {PHASES.map((phase, i) => {
          const status = getStepStatus(i, currentIndex, isRunning, isComplete);

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
                        getStepStatus(i + 1, currentIndex, isRunning, isComplete) !== "pending"
                          ? pwc.success
                          : pwc.grey200,
                    }}
                  />
                )}
              </div>

              <div
                style={
                  status === "completed"
                    ? styles.completedLabel
                    : status === "active"
                      ? styles.activeLabel
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
