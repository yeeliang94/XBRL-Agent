import { describe, test, expect, beforeEach, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, act, screen, waitFor } from "@testing-library/react";
import type { SSEEvent, RunConfigPayload } from "../lib/types";
import type { SSEFailureKind } from "../lib/sse";

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
let captureOnTransportError: ((error: string, kind: SSEFailureKind) => void) | null = null;

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    // Auth gate: resolve as a signed-in dev user so the app shell renders
    // (otherwise the boot /api/auth/me check would show the login page).
    getAuthMe: vi.fn(async () => ({
      email: "dev@localhost",
      display_name: "Dev",
      provider: "dev",
    })),
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
      tolerance_rm: 1,
    })),
    uploadPdf: vi.fn(async () => ({ session_id: "sess_1", filename: "FINCO.pdf" })),
    fetchRuns: vi.fn(async () => ({
      runs: [], total: 0, limit: 50, offset: 0,
    })),
    fetchRunDetail: vi.fn(async (id: number) => ({
      id,
      created_at: "2026-08-25T00:00:00Z",
      pdf_filename: "FINCO.pdf",
      status: "running",
      session_id: "sess_1",
      output_dir: "/tmp/out",
      merged_workbook_path: null,
      scout_enabled: false,
      started_at: "2026-08-25T00:00:00Z",
      ended_at: null,
      config: {},
      agents: [],
      cross_checks: [],
    })),
  };
});

vi.mock("../lib/sse", () => ({
  canResumeRunAfterSSEFailure: (kind: SSEFailureKind, runId: number | null) =>
    kind === "transport" && runId != null,
  createMultiAgentSSE: (
    _sessionId: string,
    _config: RunConfigPayload,
    onEvent: (event: SSEEvent) => void,
    _onDone: () => void,
    onError: (error: string, kind: SSEFailureKind) => void,
  ) => {
    captureOnEvent = onEvent;
    captureOnTransportError = onError;
    return new AbortController();
  },
}));

describe("App — live activity integration", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    captureOnEvent = null;
    captureOnTransportError = null;
    cleanup();
  });
  afterEach(() => {
    cleanup();
  });

  test("live extract view renders a flat activity sentence when a tool_call arrives", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    // 1. Upload a PDF via the hidden file input.
    fireEvent.click(screen.getByRole("link", { name: /new extraction/i }));
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
        const btn = screen.queryByRole("button", { name: /start extraction/i });
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
    // in toolTimeline and render as an update in the unified live stream.
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

    // 5. Assertions: the tool action is a flat update, not a tool card or
    // legacy chat-feed row.
    await waitFor(() => {
      expect(screen.getByRole("region", { name: /live activity/i })).toBeInTheDocument();
      expect(screen.getByTestId("activity-sentence")).toBeInTheDocument();
    });
    expect(screen.getAllByText(/Reading template/i).length).toBeGreaterThan(0);
    expect(screen.queryByTestId("tool-card")).toBeNull();
    // Legacy ChatFeed header must be gone — we stripped the whole component.
    expect(screen.queryByText(/Chat Feed/i)).toBeNull();
  });

  test("figures review uses the full workspace width with navigation expanded", async () => {
    window.history.replaceState({}, "", "/concepts/42");
    const { default: App } = await import("../App");
    render(<App />);

    const main = await waitFor(() => {
      const element = document.getElementById("main-content");
      if (!element) throw new Error("Main content not ready");
      return element;
    });
    expect(main).toHaveStyle({ maxWidth: "100%" });
  });

  test("lost live stream resumes monitoring from the durable run detail", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    fireEvent.click(screen.getByRole("link", { name: /new extraction/i }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(fileInput, {
        target: { files: [new File(["x"], "FINCO.pdf", { type: "application/pdf" })] },
      });
    });
    const runButton = await waitFor(() =>
      screen.getByRole("button", { name: /start extraction/i }),
    );
    await act(async () => fireEvent.click(runButton));

    await act(async () => {
      captureOnEvent!({
        event: "status",
        data: {
          phase: "starting",
          message: "Starting",
          run_id: 321,
        },
        timestamp: Date.now() / 1000,
      });
    });
    await act(async () => {
      captureOnTransportError!("The connection to the run was lost.", "transport");
    });

    await waitFor(() => {
      expect(window.location.pathname).toBe("/history/321");
    });
    expect(screen.queryByText("The connection to the run was lost.")).toBeNull();
  });

  test("New extraction preserves the active run URL while work is streaming", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    fireEvent.click(screen.getByRole("link", { name: /new extraction/i }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(fileInput, {
        target: { files: [new File(["x"], "FINCO.pdf", { type: "application/pdf" })] },
      });
    });
    const runButton = await waitFor(() =>
      screen.getByRole("button", { name: /start extraction/i }),
    );
    fireEvent.click(runButton);
    await waitFor(() => expect(captureOnEvent).not.toBeNull());
    await screen.findByRole("button", { name: /stop all/i });
    await act(async () => {
      captureOnEvent!({
        event: "status",
        data: { phase: "starting", message: "Starting", run_id: 99 },
        timestamp: Date.now() / 1000,
      });
    });
    expect(window.location.pathname).toMatch(/^\/(?:run|history)\/99$/);
    const activeRunPath = window.location.pathname;

    fireEvent.click(screen.getByRole("link", { name: /new extraction/i }));

    await waitFor(() => expect(window.location.pathname).toBe(activeRunPath));
  });

  // ---------------------------------------------------------------------------
  // Peer-review HIGH #2 regression: a failed PATCH before /start must
  // surface a visible error and NOT proceed to start the SSE stream.
  // Otherwise the run would either silently use stale config or fail
  // server-side after the UI has flipped into the running state.
  // ---------------------------------------------------------------------------
  test("draft start aborts and shows error when patchRunConfig rejects", async () => {
    vi.resetModules();
    captureOnEvent = null;

    let sseFactoryCalled = false;
    vi.doMock("../lib/sse", () => ({
      createMultiAgentSSE: () => {
        sseFactoryCalled = true;
        return new AbortController();
      },
      createMultiAgentSSEByRunId: () => {
        sseFactoryCalled = true;
        return new AbortController();
      },
      patchRunConfig: vi.fn(async () => {
        throw new Error("Backend exploded saving config");
      }),
    }));
    vi.doMock("../lib/api", async () => {
      const actual = await vi.importActual<typeof import("../lib/api")>(
        "../lib/api",
      );
      return {
        ...actual,
        getAuthMe: vi.fn(async () => ({
          email: "dev@localhost", display_name: "Dev", provider: "dev",
        })),
        getSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
        })),
        getExtendedSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
          available_models: [], default_models: {},
          tolerance_rm: 1,
        })),
        uploadPdf: vi.fn(async () => ({
          session_id: "sess_1", filename: "FINCO.pdf", run_id: 99,
        })),
      };
    });

    const { default: App } = await import("../App");
    window.history.replaceState({}, "", "/");
    render(<App />);

    // Upload to land us on /run/99 with a session+filename.
    fireEvent.click(screen.getByRole("link", { name: /new extraction/i }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    const file = new File(["x"], "FINCO.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });
    const runButton = await waitFor(() => {
      const btn = screen.queryByRole("button", { name: /start extraction/i });
      if (!btn) throw new Error("Run button not ready");
      return btn;
    }, { timeout: 2000 });

    // Click Run → handleMultiRun → PATCH rejects.
    await act(async () => {
      fireEvent.click(runButton);
    });
    // Microtask drain so the dispatched error event commits.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    // The SSE factory must NOT have been called — start was blocked.
    expect(sseFactoryCalled).toBe(false);
    // The error message must be visible (one or more places — the
    // ExtractPage error box AND PreRunPanel both render run errors).
    expect(
      screen.getAllByText(/backend exploded saving config/i).length,
    ).toBeGreaterThan(0);
  });
});
