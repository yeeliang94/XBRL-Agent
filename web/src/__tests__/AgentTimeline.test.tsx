// Phase 3 tests for AgentTimeline — the single replacement for ChatFeed.
// One row per ToolTimelineEntry plus a terminal complete/error row.
import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { AgentTimeline } from "../components/AgentTimeline";
import type { ToolTimelineEntry, SSEEvent } from "../lib/types";

function makeEntry(partial: Partial<ToolTimelineEntry>): ToolTimelineEntry {
  return {
    tool_call_id: "tc_0",
    tool_name: "read_template",
    args: {},
    result_summary: null,
    duration_ms: null,
    startTime: 0,
    endTime: null,
    phase: null,
    ...partial,
  };
}

describe("AgentTimeline", () => {
  test("Step 3.1 — empty state renders 'Waiting for the agent to start'", () => {
    render(<AgentTimeline events={[]} toolTimeline={[]} isRunning={false} />);
    expect(screen.getByText(/Waiting for the agent to start/)).toBeInTheDocument();
  });

  test("timeline container keeps inner padding without adding another border", () => {
    const { container } = render(
      <AgentTimeline events={[]} toolTimeline={[makeEntry({ result_summary: "ok", endTime: 1 })]} isRunning={false} />,
    );
    const scrollArea = container.querySelector(".agent-scroll") as HTMLElement;
    expect(scrollArea).toBeTruthy();
    expect(scrollArea.style.padding).toBe("12px");
    expect(scrollArea.style.border).toBe("");
  });

  test("Step 3.3 — one tool-card row per timeline entry", () => {
    const timeline: ToolTimelineEntry[] = [
      makeEntry({ tool_call_id: "a", tool_name: "read_template", result_summary: "ok", endTime: 1 }),
      makeEntry({ tool_call_id: "b", tool_name: "view_pdf_pages", args: { pages: [1] }, result_summary: "ok", endTime: 2 }),
      makeEntry({ tool_call_id: "c", tool_name: "fill_workbook" }), // active
    ];
    const { container } = render(
      <AgentTimeline events={[]} toolTimeline={timeline} isRunning={true} />,
    );
    const cards = container.querySelectorAll("[data-testid='tool-card']");
    expect(cards.length).toBe(3);
    // First two are done, third is active.
    expect(cards[0].getAttribute("data-state")).toBe("done");
    expect(cards[1].getAttribute("data-state")).toBe("done");
    expect(cards[2].getAttribute("data-state")).toBe("active");
  });

  test("Step 3.5 — successful complete event renders a completed terminal row", () => {
    // Cast through unknown: the test only needs `success` to drive the
    // terminal row. Full AgentCompleteData would demand agent_id etc. we
    // don't care about here.
    const events = [
      { event: "complete", data: { success: true }, timestamp: 1 },
    ] as unknown as SSEEvent[];
    render(<AgentTimeline events={events} toolTimeline={[]} isRunning={false} />);
    expect(screen.getByText(/Run finished/i)).toBeInTheDocument();
    expect(screen.getByText(/Completed/i)).toBeInTheDocument();
  });

  test("successful complete event with warnings surfaces them below the row", () => {
    // Peer-review finding #3: notes agents emit non-fatal diagnostics on
    // success ("borderline fuzzy match", "writer: unresolvable label X",
    // "3 of 5 sub-agent(s) failed — partial coverage only"). Previously
    // these were dropped on the floor — the terminal row rendered a
    // clean "Completed" badge so partial-success runs looked green.
    const warnings = [
      "writer: unresolvable label 'Foo'",
      "borderline fuzzy match: 'Bar' -> 'Baz' (score 0.80)",
    ];
    const events = [
      { event: "complete", data: { success: true, warnings }, timestamp: 1 },
    ] as unknown as SSEEvent[];
    const { container } = render(
      <AgentTimeline events={events} toolTimeline={[]} isRunning={false} />,
    );

    // Row still signals "done" at the container level but with a warnings
    // marker so CSS / selectors can distinguish the state.
    const row = container.querySelector("[data-terminal='done-with-warnings']");
    expect(row).toBeTruthy();

    // Badge reflects the warning count so the signal is visible at a glance
    // even before the operator expands the bullet list.
    expect(screen.getByText(/Completed · 2 warnings/i)).toBeInTheDocument();
    // Each warning renders as its own bullet in the warnings block.
    expect(screen.getByRole("note", { name: /run warnings/i })).toBeInTheDocument();
    for (const w of warnings) {
      expect(screen.getByText(w)).toBeInTheDocument();
    }
  });

  test("successful complete WITHOUT warnings keeps the clean green badge", () => {
    // Face-statement complete events don't carry a warnings field — make
    // sure the presence of the new code path doesn't regress them.
    const events = [
      { event: "complete", data: { success: true }, timestamp: 1 },
    ] as unknown as SSEEvent[];
    const { container } = render(
      <AgentTimeline events={events} toolTimeline={[]} isRunning={false} />,
    );
    expect(container.querySelector("[data-terminal='done']")).toBeTruthy();
    expect(container.querySelector("[data-terminal='done-with-warnings']")).toBeNull();
    expect(screen.queryByRole("note", { name: /run warnings/i })).toBeNull();
  });

  test("Step 3.5 — failed complete event shows the error in red", () => {
    const events = [
      { event: "complete", data: { success: false, error: "boom" }, timestamp: 1 },
    ] as unknown as SSEEvent[];
    const { container } = render(
      <AgentTimeline events={events} toolTimeline={[]} isRunning={false} />,
    );
    expect(screen.getByText(/boom/)).toBeInTheDocument();
    const row = container.querySelector("[data-terminal='error']") as HTMLElement;
    expect(row).toBeTruthy();
  });

  test("run_complete with success:true renders the completed terminal row", () => {
    // Peer-review fix: in the global fallback path the terminal event is
    // run_complete, not complete. The finder must prefer it over the last
    // per-agent complete in the event tail.
    const events = [
      { event: "complete", data: { success: true }, timestamp: 1 },
      { event: "run_complete", data: { success: true }, timestamp: 2 },
    ] as unknown as SSEEvent[];
    const { container } = render(
      <AgentTimeline events={events} toolTimeline={[]} isRunning={false} />,
    );
    expect(screen.getByText(/Completed/i)).toBeInTheDocument();
    expect(container.querySelector("[data-terminal='done']")).toBeTruthy();
  });

  test("run_complete with success:false and merge_errors shows the first error", () => {
    const events = [
      {
        event: "run_complete",
        data: { success: false, merge_errors: ["Disk full", "Permission denied"] },
        timestamp: 1,
      },
    ] as unknown as SSEEvent[];
    const { container } = render(
      <AgentTimeline events={events} toolTimeline={[]} isRunning={false} />,
    );
    expect(screen.getByText(/Disk full/)).toBeInTheDocument();
    expect(container.querySelector("[data-terminal='error']")).toBeTruthy();
  });

  // Peer-review regression: the validation-fail paths in server.py emit
  // `run_complete { success: false, message: "..." }` with no merge_errors
  // field. Without this branch the terminal row fell back to a generic
  // "Failed" and the actionable reason was dropped on the floor.
  test("run_complete with success:false and message surfaces the message", () => {
    const events = [
      {
        event: "run_complete",
        data: { success: false, message: "Model setup failed: missing API key" },
        timestamp: 1,
      },
    ] as unknown as SSEEvent[];
    const { container } = render(
      <AgentTimeline events={events} toolTimeline={[]} isRunning={false} />,
    );
    expect(screen.getByText(/Model setup failed: missing API key/)).toBeInTheDocument();
    expect(container.querySelector("[data-terminal='error']")).toBeTruthy();
  });

  test("run_complete failed: message wins over merge_errors[0]", () => {
    // Both fields present — the specific `message` should beat the generic
    // rollup label. Covers the ordering in TerminalRow.
    const events = [
      {
        event: "run_complete",
        data: {
          success: false,
          message: "Unknown statement type: SOXX",
          merge_errors: ["merge failed"],
        },
        timestamp: 1,
      },
    ] as unknown as SSEEvent[];
    render(<AgentTimeline events={events} toolTimeline={[]} isRunning={false} />);
    expect(screen.getByText(/Unknown statement type: SOXX/)).toBeInTheDocument();
    expect(screen.queryByText(/merge failed/)).toBeNull();
  });

  test("Step 3.5 — plain error event shows the message in red", () => {
    const events = [
      { event: "error", data: { message: "fatal" }, timestamp: 1 },
    ] as unknown as SSEEvent[];
    const { container } = render(
      <AgentTimeline events={events} toolTimeline={[]} isRunning={false} />,
    );
    expect(screen.getByText(/fatal/)).toBeInTheDocument();
    expect(container.querySelector("[data-terminal='error']")).toBeTruthy();
  });

  // --- Step 3.7: auto-scroll behaviour ---

  describe("auto-scroll", () => {
    // jsdom doesn't implement scroll layout, so we stub the scroll properties
    // on the container element to drive the "at bottom" check.
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
      vi.restoreAllMocks();
    });

    function stubScrollGeometry(el: HTMLElement, atBottom: boolean) {
      // scrollHeight - scrollTop - clientHeight < 40 means "at bottom".
      Object.defineProperty(el, "scrollHeight", { configurable: true, value: 1000 });
      Object.defineProperty(el, "clientHeight", { configurable: true, value: 500 });
      Object.defineProperty(el, "scrollTop", {
        configurable: true,
        writable: true,
        value: atBottom ? 500 : 0, // 500 → at bottom; 0 → user scrolled up
      });
    }

    test("scrollTop updates to bottom when re-rendered while user is near bottom", () => {
      const initial = [
        makeEntry({ tool_call_id: "1", result_summary: "ok", endTime: 1 }),
        makeEntry({ tool_call_id: "2", result_summary: "ok", endTime: 2 }),
        makeEntry({ tool_call_id: "3", result_summary: "ok", endTime: 3 }),
      ];
      const { container, rerender } = render(
        <AgentTimeline events={[]} toolTimeline={initial} isRunning={true} />,
      );
      const scrollArea = container.querySelector(".agent-scroll") as HTMLElement;
      expect(scrollArea).toBeTruthy();
      stubScrollGeometry(scrollArea, /*atBottom=*/ true);

      const next = [...initial, makeEntry({ tool_call_id: "4" })];
      act(() => {
        rerender(<AgentTimeline events={[]} toolTimeline={next} isRunning={true} />);
      });

      // After re-render, scrollTop should have been set to scrollHeight (1000).
      expect(scrollArea.scrollTop).toBe(1000);
    });

    test("scrollTop updates when the terminal event arrives (even if toolTimeline is unchanged)", () => {
      // Peer-review fix: previously the auto-scroll effect keyed only on
      // toolTimeline.length, so a complete/error row appearing after the
      // final tool_result could land below the fold.
      const timeline = [
        makeEntry({ tool_call_id: "1", result_summary: "ok", endTime: 1 }),
        makeEntry({ tool_call_id: "2", result_summary: "ok", endTime: 2 }),
      ];
      const { container, rerender } = render(
        <AgentTimeline events={[]} toolTimeline={timeline} isRunning={true} />,
      );
      const scrollArea = container.querySelector(".agent-scroll") as HTMLElement;
      stubScrollGeometry(scrollArea, /*atBottom=*/ true);
      // Reset scrollTop so we can tell whether the effect fired.
      scrollArea.scrollTop = 0;

      const terminalEvent = {
        event: "complete",
        data: { success: true },
        timestamp: 3,
      } as unknown as SSEEvent;
      act(() => {
        rerender(
          <AgentTimeline
            events={[terminalEvent]}
            toolTimeline={timeline}
            isRunning={false}
          />,
        );
      });

      // Effect should have fired because `terminal` changed, even though
      // toolTimeline.length didn't.
      expect(scrollArea.scrollTop).toBe(1000);
    });

    test("scrollTop unchanged when user has scrolled up", () => {
      const initial = [
        makeEntry({ tool_call_id: "1", result_summary: "ok", endTime: 1 }),
        makeEntry({ tool_call_id: "2", result_summary: "ok", endTime: 2 }),
        makeEntry({ tool_call_id: "3", result_summary: "ok", endTime: 3 }),
      ];
      const { container, rerender } = render(
        <AgentTimeline events={[]} toolTimeline={initial} isRunning={true} />,
      );
      const scrollArea = container.querySelector(".agent-scroll") as HTMLElement;
      // Stub geometry first so onScroll sees "not at bottom".
      stubScrollGeometry(scrollArea, /*atBottom=*/ false);
      // Fire the scroll event so the component records "user scrolled up".
      act(() => {
        scrollArea.dispatchEvent(new Event("scroll"));
      });

      const next = [...initial, makeEntry({ tool_call_id: "4" })];
      act(() => {
        rerender(<AgentTimeline events={[]} toolTimeline={next} isRunning={true} />);
      });

      // scrollTop stays at 0 — we didn't force it back to bottom.
      expect(scrollArea.scrollTop).toBe(0);
    });
  });
});
