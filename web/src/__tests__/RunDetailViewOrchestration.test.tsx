/**
 * Pinning test for the Overview tab's Orchestration badge.
 *
 * The badge mirrors the persisted `runs.orchestration` value (DB schema
 * v10). Default ('split') and the experimental ('monolith') label are
 * the two states we surface in History.
 */
import { describe, test, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { RunDetailView } from "../components/RunDetailView";
import type { RunDetailJson } from "../lib/types";

function makeDetail(
  configOverride: Record<string, unknown> = {},
): RunDetailJson {
  return {
    id: 1,
    created_at: "2026-05-28T00:00:00Z",
    pdf_filename: "x.pdf",
    status: "completed",
    session_id: "s",
    output_dir: "/tmp/s",
    merged_workbook_path: "/tmp/s/filled.xlsx",
    scout_enabled: false,
    started_at: "2026-05-28T00:00:00Z",
    ended_at: "2026-05-28T00:01:00Z",
    config: {
      statements: ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"],
      variants: {},
      models: {},
      use_scout: false,
      filing_level: "company",
      filing_standard: "mfrs",
      ...configOverride,
    },
    agents: [],
    cross_checks: [],
  };
}


describe("RunDetailView — Orchestration badge", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  test("shows 'Split (default)' when orchestration is omitted (legacy row)", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.getByText(/Orchestration/i)).toBeInTheDocument();
    expect(screen.getByText(/Split \(default\)/)).toBeInTheDocument();
  });

  test("shows 'Monolith (experimental)' when orchestration is monolith", () => {
    render(
      <RunDetailView
        detail={makeDetail({ orchestration: "monolith" })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.getByText(/Monolith \(experimental\)/i)).toBeInTheDocument();
  });
});
