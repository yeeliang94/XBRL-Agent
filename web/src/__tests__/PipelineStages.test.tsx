import { describe, test, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PipelineStages } from "../components/PipelineStages";

const PHASE_LABELS = [
  "Reading template",
  "Viewing PDF",
  "Filling workbook",
  "Verifying",
  "Complete",
];

describe("PipelineStages", () => {
  test("renders all 5 phases as step items", () => {
    render(
      <PipelineStages currentPhase={null} isRunning={false} isComplete={false} />,
    );
    for (const label of PHASE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  test("marks completed phases with green checkmark", () => {
    // When currentPhase is "filling_workbook", reading_template and viewing_pdf are done
    const { container } = render(
      <PipelineStages currentPhase="filling_workbook" isRunning={true} isComplete={false} />,
    );
    // Completed steps have a checkmark (✓)
    const checks = container.querySelectorAll("[data-testid='step-check']");
    expect(checks.length).toBe(2); // reading_template + viewing_pdf
  });

  test("marks active phase with pulsing orange dot", () => {
    const { container } = render(
      <PipelineStages currentPhase="viewing_pdf" isRunning={true} isComplete={false} />,
    );
    const activeDot = container.querySelector("[data-testid='step-active']");
    expect(activeDot).toBeInTheDocument();
    expect(activeDot?.getAttribute("style")).toContain("animation");
  });

  test("marks pending phases with grey circle", () => {
    const { container } = render(
      <PipelineStages currentPhase="reading_template" isRunning={true} isComplete={false} />,
    );
    // 4 pending: viewing_pdf, filling_workbook, verifying, complete
    const pending = container.querySelectorAll("[data-testid='step-pending']");
    expect(pending.length).toBe(4);
  });

  test("draws connector lines between steps", () => {
    const { container } = render(
      <PipelineStages currentPhase="viewing_pdf" isRunning={true} isComplete={false} />,
    );
    // 4 connector lines between 5 steps
    const connectors = container.querySelectorAll("[data-testid='connector']");
    expect(connectors.length).toBe(4);
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
    const checks = container.querySelectorAll("[data-testid='step-check']");
    expect(checks.length).toBe(5);
  });

  test("applies PwC theme colors (orange500 active, success completed, grey300 pending)", () => {
    const { container } = render(
      <PipelineStages currentPhase="viewing_pdf" isRunning={true} isComplete={false} />,
    );
    // Active step should use orange400 (#FE7C39 → rgb(254, 124, 57))
    const activeDot = container.querySelector("[data-testid='step-active']");
    expect(activeDot?.getAttribute("style")).toContain("rgb(254, 124, 57)");

    // Completed step should use success green (#16A34A → rgb(22, 163, 74))
    const check = container.querySelector("[data-testid='step-check']");
    expect(check?.getAttribute("style")).toContain("rgb(22, 163, 74)");

    // Pending step should use grey300 (#CBD1D6 → rgb(203, 209, 214))
    const pending = container.querySelector("[data-testid='step-pending']");
    expect(pending?.getAttribute("style")).toContain("rgb(203, 209, 214)");
  });
});
