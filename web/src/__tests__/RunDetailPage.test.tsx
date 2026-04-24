import { describe, test, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { RunDetailPage } from "../components/RunDetailPage";
import type { RunDetailJson } from "../lib/types";

// ---------------------------------------------------------------------------
// RunDetailPage — full-page replacement for the old RunDetailModal. Renders
// a header with a "Back to history" button + run title, then the embedded
// RunDetailView content (no modal chrome). Deep-linking lives at
// /history/<id>, so this component has to stand on its own (no overlay
// dismissal, no keyboard esc — just a back button).
// ---------------------------------------------------------------------------

function makeDetail(): RunDetailJson {
  return {
    id: 42,
    created_at: "2026-04-10T09:30:00Z",
    pdf_filename: "FINCO.pdf",
    status: "completed",
    session_id: "sess-42",
    output_dir: "/tmp/out/sess-42",
    merged_workbook_path: "/tmp/out/sess-42/filled.xlsx",
    scout_enabled: false,
    started_at: "2026-04-10T09:30:00Z",
    ended_at: "2026-04-10T09:31:00Z",
    config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
    agents: [],
    cross_checks: [],
  } as unknown as RunDetailJson;
}

beforeEach(() => {
  // jsdom needs a ResizeObserver stub for NotesReviewTab's TipTap editor —
  // even with no rows, the component boots.
  if (!(globalThis as any).ResizeObserver) {
    (globalThis as any).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  // Stub the notes_cells fetch so the embedded NotesReviewTab stops
  // spinning — empty response is fine for these shell tests.
  globalThis.fetch = vi.fn(async () =>
    new Response(JSON.stringify({ sheets: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  ) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RunDetailPage", () => {
  test("renders a back-to-history button", () => {
    render(
      <RunDetailPage
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onBack={() => {}}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    // The back button is the user's only path out — assert it's present
    // and labelled accessibly.
    expect(
      screen.getByRole("button", { name: /back to history/i }),
    ).toBeInTheDocument();
  });

  test("clicking back calls onBack", () => {
    const onBack = vi.fn();
    render(
      <RunDetailPage
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onBack={onBack}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /back to history/i }),
    );
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  test("shows loading state while detail is fetching", () => {
    render(
      <RunDetailPage
        detail={null}
        isLoading={true}
        error={null}
        onBack={() => {}}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText(/loading run details/i)).toBeInTheDocument();
  });

  test("shows error banner when error prop is set", () => {
    render(
      <RunDetailPage
        detail={null}
        isLoading={false}
        error="Fetch failed"
        onBack={() => {}}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText(/fetch failed/i)).toBeInTheDocument();
  });

  test("renders the embedded RunDetailView content", () => {
    render(
      <RunDetailPage
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onBack={() => {}}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    // Filename is RunDetailView's header — a hallmark the inner view
    // actually mounted rather than just the shell.
    expect(screen.getByText("FINCO.pdf")).toBeInTheDocument();
  });
});
