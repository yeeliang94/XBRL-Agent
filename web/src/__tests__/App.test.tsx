import { describe, test, expect, beforeEach, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, act, screen, waitFor } from "@testing-library/react";
import type { SSEEvent, RunConfigPayload } from "../lib/types";

// ---------------------------------------------------------------------------
// App-level integration tests — guarantee the live extract view renders
// tool-card rows via AgentTimeline when an SSE tool_call event arrives.
//
// We stub both the settings API (so PreRunPanel's mount effect is a no-op)
// and the SSE factory (so we can feed synthetic events into the reducer
// without standing up a real backend). The stubbed factory captures the
// event callback on first call, letting each test simulate the agent stream.
// ---------------------------------------------------------------------------

let captureOnEvent: ((event: SSEEvent) => void) | null = null;

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getSettings: vi.fn(async () => ({
      model: "x",
      proxy_url: "",
      api_key_set: true,
      api_key_preview: "",
    })),
    getExtendedSettings: vi.fn(async () => ({
      model: "x",
      proxy_url: "",
      api_key_set: true,
      api_key_preview: "",
      available_models: [],
      default_models: {},
      scout_enabled_default: false,
      tolerance_rm: 1,
    })),
    uploadPdf: vi.fn(async () => ({ session_id: "sess_1", filename: "FINCO.pdf" })),
  };
});

vi.mock("../lib/sse", () => ({
  createMultiAgentSSE: (
    _sessionId: string,
    _config: RunConfigPayload,
    onEvent: (event: SSEEvent) => void,
  ) => {
    captureOnEvent = onEvent;
    return new AbortController();
  },
}));

describe("App — AgentTimeline integration", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    captureOnEvent = null;
    cleanup();
  });
  afterEach(() => {
    cleanup();
  });

  test("live extract view renders a tool-card row when a tool_call event arrives", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    // 1. Upload a PDF via the hidden file input.
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    expect(fileInput).toBeTruthy();
    const file = new File(["dummy"], "FINCO.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });

    // 2. Wait deterministically for the PreRunPanel's settings fetch to
    // resolve and the Run button to appear. Replaces the previous silent
    // early-return that could turn the whole test into a no-op.
    const runButton = await waitFor(
      () => {
        const btn = screen.queryByRole("button", { name: /run/i });
        if (!btn) throw new Error("Run button not ready");
        return btn;
      },
      { timeout: 2000 },
    );

    // 3. Click Run — this invokes the mocked SSE factory and captures onEvent.
    await act(async () => {
      fireEvent.click(runButton);
    });
    expect(captureOnEvent).toBeTruthy();

    // 4. Feed a synthetic status + tool_call through the captured callback.
    // The status event establishes the agent tab; the tool_call should land
    // in toolTimeline and render as a ToolCallCard row inside AgentTimeline.
    await act(async () => {
      captureOnEvent!({
        event: "status",
        data: {
          phase: "reading_template",
          message: "",
          agent_id: "sofp_0",
          agent_role: "SOFP",
        },
        timestamp: Date.now() / 1000,
      });
    });
    await act(async () => {
      captureOnEvent!({
        event: "tool_call",
        data: {
          tool_name: "read_template",
          tool_call_id: "tc_1",
          args: { path: "/x/01-SOFP-CuNonCu.xlsx" },
          agent_id: "sofp_0",
        },
        timestamp: Date.now() / 1000,
      });
    });

    // 5. Assertions: the tool row is rendered via the new AgentTimeline /
    // ToolCallCard stack, NOT the legacy ChatFeed chrome. Multiple cards
    // may render (tabbed path + legacy single-agent path both visible in
    // this state) so we assert presence, not uniqueness.
    await waitFor(() => {
      expect(screen.getAllByTestId("tool-card").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("Reading template").length).toBeGreaterThan(0);
    // Legacy ChatFeed header must be gone — we stripped the whole component.
    expect(screen.queryByText(/Chat Feed/i)).toBeNull();
  });
});
