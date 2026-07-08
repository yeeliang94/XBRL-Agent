import { describe, test, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { NeedsAttentionPanel } from "../components/NeedsAttentionPanel";
import type { CrossCheckResult } from "../lib/types";
import type { CoverageNavRow } from "../components/NotesCoverageNav";

afterEach(() => cleanup());

const CHECK: CrossCheckResult = {
  name: "sofp_balance",
  status: "failed",
  message: "assets exceed equity+liabilities",
  target_sheet: "SOFP-CuNonCu",
  target_row: 23,
} as CrossCheckResult;

const GAP: CoverageNavRow = {
  note_num: 8,
  title: "Contingencies",
  status: "missing",
  reviewer_verdict: null,
  placements: [],
  page_lo: 20,
  page_hi: null,
};

describe("NeedsAttentionPanel", () => {
  test("shows an all-clear line when nothing is outstanding", () => {
    render(
      <NeedsAttentionPanel
        failingChecks={[]}
        onSelectCheck={() => {}}
        coverageGaps={[]}
        onSelectNote={() => {}}
        openConflicts={0}
        reconciliation={<div data-testid="recon" />}
      />,
    );
    expect(screen.getByTestId("needs-attention-clear")).toBeTruthy();
    // The reconciliation node is not rendered on an all-clear run.
    expect(screen.queryByTestId("recon")).toBeNull();
    expect(screen.queryByTestId("needs-attention")).toBeNull();
  });

  test("counts all three feeds in the header", () => {
    render(
      <NeedsAttentionPanel
        failingChecks={[CHECK]}
        onSelectCheck={() => {}}
        coverageGaps={[GAP]}
        onSelectNote={() => {}}
        openConflicts={2}
        reconciliation={<div data-testid="recon" />}
      />,
    );
    // 1 check + 1 gap + 2 conflicts = 4.
    expect(screen.getByTestId("needs-attention-count").textContent).toContain(
      "Needs attention (4)",
    );
    // Conflicts section renders the passed-in reconciliation node verbatim.
    expect(screen.getByTestId("recon")).toBeTruthy();
  });

  test("clicking a check jumps to its target cell", () => {
    const onSelectCheck = vi.fn();
    render(
      <NeedsAttentionPanel
        failingChecks={[CHECK]}
        onSelectCheck={onSelectCheck}
        coverageGaps={[]}
        onSelectNote={() => {}}
        openConflicts={0}
        reconciliation={null}
      />,
    );
    fireEvent.click(screen.getByTestId("attention-check-0"));
    expect(onSelectCheck).toHaveBeenCalledWith("SOFP-CuNonCu", 23);
  });

  test("clicking a gap note reports the row", () => {
    const onSelectNote = vi.fn();
    render(
      <NeedsAttentionPanel
        failingChecks={[]}
        onSelectCheck={() => {}}
        coverageGaps={[GAP]}
        onSelectNote={onSelectNote}
        openConflicts={0}
        reconciliation={null}
      />,
    );
    fireEvent.click(screen.getByTestId("attention-note-8"));
    expect(onSelectNote).toHaveBeenCalledWith(GAP);
  });

  test("a check without a target cell is listed but not clickable", () => {
    const onSelectCheck = vi.fn();
    const untargeted = { ...CHECK, target_sheet: null, target_row: null } as CrossCheckResult;
    render(
      <NeedsAttentionPanel
        failingChecks={[untargeted]}
        onSelectCheck={onSelectCheck}
        coverageGaps={[]}
        onSelectNote={() => {}}
        openConflicts={0}
        reconciliation={null}
      />,
    );
    const btn = screen.getByTestId("attention-check-0") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain("assets exceed equity+liabilities");
  });
});
