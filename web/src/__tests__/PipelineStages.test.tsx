import { describe, test, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PipelineStages } from "../components/PipelineStages";

const PHASE_LABELS = [
  "Prepare document",
  "Confirm setup",
  "Extract",
  "Check",
  "Review",
  "Ready",
];

describe("PipelineStages", () => {
  test("renders the continuous preparation-to-ready workflow", () => {
    render(
      <PipelineStages currentPhase={null} isRunning={false} isComplete={false} />,
    );
    for (const label of PHASE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  test("marks completed phases with green completed indicators", () => {
    // When currentPhase is "filling_workbook", reading_template and viewing_pdf are done
    const { container } = render(
      <PipelineStages currentPhase="filling_workbook" isRunning={true} isComplete={false} />,
    );
    const checks = container.querySelectorAll("[data-testid='step-complete']");
    expect(checks.length).toBe(3); // preparation + confirmation + extraction
  });

  test("marks active phase with pulsing orange dot", () => {
    const { container } = render(
      <PipelineStages currentPhase="viewing_pdf" isRunning={true} isComplete={false} />,
    );
    const activeDot = container.querySelector("[data-testid='step-active']");
    expect(activeDot).toBeInTheDocument();
    expect(activeDot).toHaveClass("pwc-working-indicator");
  });

  test("marks pending phases with grey circle", () => {
    const { container } = render(
      <PipelineStages currentPhase="reading_template" isRunning={true} isComplete={false} />,
    );
    // Check, Review and Ready remain pending while extraction starts.
    const pending = container.querySelectorAll("[data-testid='step-pending']");
    expect(pending.length).toBe(3);
  });

  test("draws connector lines between steps", () => {
    const { container } = render(
      <PipelineStages currentPhase="viewing_pdf" isRunning={true} isComplete={false} />,
    );
    // 5 connector lines between 6 steps
    const connectors = container.querySelectorAll("[data-testid='connector']");
    expect(connectors.length).toBe(5);
  });

  test("shows no active phase when isRunning=false and isComplete=false", () => {
    const { container } = render(
      <PipelineStages currentPhase={null} isRunning={false} isComplete={false} />,
    );
    const activeDot = container.querySelector("[data-testid='step-active']");
    expect(activeDot).not.toBeInTheDocument();
  });

  test("shows all phases complete when isComplete=true", () => {
    const { container } = render(
      <PipelineStages currentPhase="complete" isRunning={false} isComplete={true} />,
    );
    const checks = container.querySelectorAll("[data-testid='step-complete']");
    expect(checks.length).toBe(6);
  });

  test("maps coordinator stages to the run-level progress story", () => {
    const { container, rerender } = render(
      <PipelineStages currentPhase="viewing_pdf" pipelineStage="cross_checking" isRunning={true} isComplete={false} />,
    );
    expect(container.querySelectorAll("[data-testid='step-complete']")).toHaveLength(3);
    expect(screen.getByText("Check")).toHaveStyle({ fontWeight: "600" });
    expect(screen.getByText("Check")).toHaveAttribute("aria-current", "step");

    rerender(
      <PipelineStages currentPhase="reading_template" pipelineStage="reviewing_notes" isRunning={true} isComplete={false} />,
    );
    expect(container.querySelectorAll("[data-testid='step-complete']")).toHaveLength(4);
    expect(screen.getByText("Review")).toHaveStyle({ fontWeight: "600" });
  });

  test("keeps run-owned source transcription within the active extraction stage", () => {
    render(
      <PipelineStages
        currentPhase={null}
        pipelineStage="transcribing_source"
        isRunning={true}
        isComplete={false}
      />,
    );
    expect(screen.getByText("Extract")).toHaveStyle({ fontWeight: "600" });
  });

  test("marks setup confirmation as an explicit action after document mapping", () => {
    const { container } = render(
      <PipelineStages
        currentPhase={null}
        preparationPhase="awaiting_confirmation"
        preparationAction="confirm_setup"
        isRunning={false}
        isComplete={false}
      />,
    );
    expect(container.querySelectorAll("[data-testid='step-complete']")).toHaveLength(1);
    expect(container.querySelector("[data-testid='step-action']")).toBeInTheDocument();
    expect(screen.getByText("Confirm setup")).toHaveAttribute("aria-current", "step");
  });

  test("applies PwC theme colors (orange500 active, success completed, grey300 pending)", () => {
    const { container } = render(
      <PipelineStages currentPhase="viewing_pdf" isRunning={true} isComplete={false} />,
    );
    // Active step should use orange400 (#FE7C39 → rgb(254, 124, 57))
    const activeDot = container.querySelector("[data-testid='step-active']");
    expect(activeDot?.getAttribute("style")).toContain("rgb(254, 124, 57)");

    // Completed step should use focused-workspace finished black.
    const check = container.querySelector("[data-testid='step-complete']");
    expect(check?.getAttribute("style")).toContain("rgb(0, 0, 0)");

    // Pending step should use focused-workspace grey300.
    const pending = container.querySelector("[data-testid='step-pending']");
    expect(pending?.getAttribute("style")).toContain("rgb(203, 209, 214)");
  });
});
