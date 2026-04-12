import type { EventPhase } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  currentPhase: EventPhase | null;
  isRunning: boolean;
  isComplete: boolean;
}

const PHASES: { key: EventPhase; label: string }[] = [
  { key: "reading_template", label: "Reading template" },
  { key: "viewing_pdf", label: "Viewing PDF" },
  { key: "filling_workbook", label: "Filling workbook" },
  { key: "verifying", label: "Verifying" },
  { key: "complete", label: "Complete" },
];

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
    display: "flex",
    flexDirection: "column" as const,
    gap: 0,
    background: pwc.white,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    boxShadow: pwc.shadow.card,
    padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
  },
  step: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    height: 32,
  },
  // Circle indicators
  completeCircle: {
    width: 24,
    height: 24,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "#F0FDF4",
    border: `2px solid ${pwc.success}`,
    flexShrink: 0,
  } as React.CSSProperties,
  completeDot: {
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: pwc.success,
  } as React.CSSProperties,
  activeCircle: {
    width: 24,
    height: 24,
    borderRadius: "50%",
    background: pwc.orange400,
    animation: "pulse-subtle 1.5s ease-in-out infinite",
    flexShrink: 0,
  } as React.CSSProperties,
  pendingCircle: {
    width: 24,
    height: 24,
    borderRadius: "50%",
    border: `2px solid ${pwc.grey300}`,
    background: "transparent",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: pwc.grey300,
    fontSize: 12,
    fontFamily: pwc.fontMono,
    flexShrink: 0,
  } as React.CSSProperties,
  // Labels
  completedLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
  },
  activeLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
  },
  pendingLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey500,
  },
  // Connector
  connector: {
    width: 2,
    height: 8,
    marginLeft: 11, // Center under 24px circle
    flexShrink: 0,
  },
};

export function PipelineStages({ currentPhase, isRunning, isComplete }: Props) {
  const currentIndex = currentPhase
    ? PHASES.findIndex((p) => p.key === currentPhase)
    : -1;

  return (
    <div style={styles.container}>
      {PHASES.map((phase, i) => {
        const status = getStepStatus(i, currentIndex, isRunning, isComplete);

        return (
          <div key={phase.key}>
            <div style={styles.step}>
              {/* Step indicator */}
              {status === "completed" && (
                <div data-testid="step-complete" style={styles.completeCircle}>
                  <span style={styles.completeDot} />
                </div>
              )}
              {status === "active" && (
                <div data-testid="step-active" style={styles.activeCircle} />
              )}
              {status === "pending" && (
                <div data-testid="step-pending" style={styles.pendingCircle}>
                  {i + 1}
                </div>
              )}

              {/* Label */}
              <span
                style={
                  status === "completed"
                    ? styles.completedLabel
                    : status === "active"
                      ? styles.activeLabel
                      : styles.pendingLabel
                }
              >
                {phase.label}
              </span>
            </div>

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
        );
      })}
    </div>
  );
}
