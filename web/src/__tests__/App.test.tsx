import { describe, test, expect, beforeEach, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, act, screen, waitFor, within } from "@testing-library/react";
import type { SSEEvent, RunConfigPayload, PreparationSnapshot } from "../lib/types";
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

const preparedSnapshot = vi.hoisted(() => ({
  attempt_id: "prepared-1", status: "succeeded", stage: "complete",
  phase: "awaiting_confirmation", action_required: "confirm_setup", message: "Prepared",
  total: 1, captured: 1, verified: 1,
} satisfies PreparationSnapshot));

let captureOnEvent: ((event: SSEEvent) => void) | null = null;
let captureOnTransportError: ((error: string, kind: SSEFailureKind) => void) | null = null;

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    apiFetch: vi.fn(async (url: string, options?: RequestInit) => {
      if (url.startsWith("/api/preparation/")) return preparedSnapshot;
      return actual.apiFetch(url, options);
    }),
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
  patchRunConfig: vi.fn(async () => ({})),
  canResumeRunAfterSSEFailure: (kind: SSEFailureKind, runId: number | null) =>
    kind === "transport" && runId != null,
  createMultiAgentSSEByRunId: (
    _runId: number,
    onEvent: (event: SSEEvent) => void,
    _onDone: () => void,
    onError: (error: string, kind: SSEFailureKind) => void,
  ) => {
    captureOnEvent = onEvent;
    captureOnTransportError = onError;
    return new AbortController();
  },
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
    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    expect(fileInput).toBeTruthy();
    const file = new File(["dummy"], "FINCO.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });

    // 2. Wait deterministically for the PreRunPanel's settings fetch to
    // resolve, preparation to finish, and Run to become enabled. Replaces the previous silent
    // early-return that could turn the whole test into a no-op.
    const runButton = await waitFor(
      () => {
        const btn = screen.queryByRole("button", { name: /start extraction/i });
        if (!btn) throw new Error("Run button not ready");
        expect(btn).toBeEnabled();
        return btn;
      },
      { timeout: 2000 },
    );

    // 3. Click Run — this invokes the mocked SSE factory and captures onEvent.
    await act(async () => {
      fireEvent.click(runButton);
    });
    expect(captureOnEvent).toBeTruthy();

    // 4. Feed a synthetic status + semantic page tool through the captured
    // callback. Raw tool operations are intentionally excluded from the
    // operator feed; page activity remains visible in plain language.
    await act(async () => {
      captureOnEvent!({
        event: "status",
        data: {
          phase: "viewing_pdf",
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
          tool_name: "view_pdf_pages",
          tool_call_id: "tc_1",
          args: { pages: [4, 5] },
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
    expect(screen.getAllByText(/Reviewing source pages 4 and 5/i).length).toBeGreaterThan(0);
    expect(screen.queryByTestId("tool-card")).toBeNull();
    // Legacy ChatFeed header must be gone — we stripped the whole component.
    expect(screen.queryByText(/Chat Feed/i)).toBeNull();
  });

  test("batch upload creates both documents and returns to In progress", async () => {
    const api = await import("../lib/api");
    vi.mocked(api.uploadPdf).mockResolvedValueOnce({ session_id: "a", filename: "A.pdf", run_id: 701 });
    vi.mocked(api.uploadPdf).mockResolvedValueOnce({ session_id: "b", filename: "B.pdf", run_id: 702 });
    const { default: App } = await import("../App");
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    const files = [new File(["a"], "A.pdf"), new File(["b"], "B.pdf")];
    await act(async () => fireEvent.change(screen.getByLabelText("Upload document"), { target: { files } }));
    expect(api.uploadPdf).toHaveBeenCalledWith(files[0]);
    expect(api.uploadPdf).toHaveBeenCalledWith(files[1]);
    expect(screen.getByRole("heading", { name: "Work queue" })).toBeInTheDocument();
    expect(within(screen.getByRole("tablist", { name: "Document lists" })).getByRole("tab", { name: /In progress/ })).toHaveAttribute("aria-selected", "true");
  });

  test("starting and reopening a running document shows its activity with review tabs", async () => {
    const { uploadPdf, fetchRuns, fetchRunDetail } = await import("../lib/api");
    vi.mocked(fetchRuns).mockImplementation(async (filters) => ({
      runs: filters?.documentGroup === "progress" ? [{
        id: 321, pdf_filename: "FINCO.pdf", status: "running",
        session_id: "sess_1", created_at: "2026-08-25T00:00:00Z",
      } as import("../lib/types").RunSummaryJson, {
        id: 322, pdf_filename: "SECOND.pdf", status: "draft",
        session_id: "sess_2", created_at: "2026-08-25T00:00:00Z",
        preparation: { status: "working", phase: "building_map" },
      } as import("../lib/types").RunSummaryJson] : [],
      total: filters?.documentGroup === "progress" ? 2 : 0, limit: 50, offset: 0,
    }));
    vi.mocked(uploadPdf).mockResolvedValueOnce({
      session_id: "sess_1", filename: "FINCO.pdf", run_id: 321,
    });
    const { default: App } = await import("../App");
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(fileInput, {
        target: { files: [new File(["x"], "FINCO.pdf", { type: "application/pdf" })] },
      });
    });
    const start = await screen.findByRole("button", { name: /start extraction/i });
    await waitFor(() => expect(start).toBeEnabled());
    await act(async () => fireEvent.click(start));
    expect(captureOnEvent).toBeTruthy();
    act(() => captureOnEvent!({
      event: "status",
      data: { phase: "starting", message: "Starting", run_id: 321 },
      timestamp: Date.now() / 1000,
    }));
    expect(screen.getByRole("button", { name: "Open FINCO.pdf" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("combobox", { name: "Switch document" })).toBeNull();
    expect(screen.queryByRole("button", { name: "All runs" })).toBeNull();
    const sidebar = screen.getByRole("navigation", { name: "Recent documents" });
    // The stage moves to the status icon and hover text; the row shows only the name.
    expect(within(sidebar).getByRole("button", { name: "Open SECOND.pdf" })).toHaveAttribute("title", "SECOND.pdf · Mapping document");
    const tabs = await screen.findByRole("tablist", { name: "Run detail sections" });
    expect(within(tabs).getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("run-detail-agents")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(screen.getByRole("button", { name: "Back to document" }));
    const returnedTabs = await screen.findByRole("tablist", { name: "Run detail sections" });
    expect(within(returnedTabs).getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("link", { name: "Work queue" }));
    const queue = await screen.findByRole("table", { name: "Documents in progress" });
    fireEvent.click(within(queue).getByRole("button", { name: "FINCO.pdf" }));
    const reopenedTabs = await screen.findByRole("tablist", { name: "Run detail sections" });
    expect(within(reopenedTabs).getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    expect(within(reopenedTabs).getByRole("tab", { name: "Overview" })).toBeEnabled();
    expect(screen.getByTestId("run-detail-agents")).toBeInTheDocument();
    vi.mocked(fetchRunDetail).mockResolvedValueOnce({
      id: 322, session_id: "sess_2", pdf_filename: "SECOND.pdf", status: "draft",
      created_at: "2026-08-25T00:00:00Z", output_dir: "", merged_workbook_path: null,
      scout_enabled: false, started_at: null, ended_at: null, config: {}, agents: [], cross_checks: [],
    });
    fireEvent.click(screen.getByRole("button", { name: "Open SECOND.pdf" }));
    expect(window.location.pathname).toBe("/run/322");
    await screen.findByRole("heading", { name: "SECOND.pdf" });
    fireEvent.click(screen.getByRole("button", { name: "Open FINCO.pdf" }));
    const switchedTabs = await screen.findByRole("tablist", { name: "Run detail sections" });
    expect(within(switchedTabs).getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    vi.mocked(fetchRuns).mockResolvedValue({ runs: [], total: 0, limit: 50, offset: 0 });

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

    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(fileInput, {
        target: { files: [new File(["x"], "FINCO.pdf", { type: "application/pdf" })] },
      });
    });
    const runButton = await waitFor(() =>
      screen.getByRole("button", { name: /start extraction/i }),
    );
    await waitFor(() => expect(runButton).toBeEnabled());
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

  test("Documents navigation detaches monitoring and permits another upload while work continues", async () => {
    const { default: App } = await import("../App");
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(fileInput, {
        target: { files: [new File(["x"], "FINCO.pdf", { type: "application/pdf" })] },
      });
    });
    const runButton = await waitFor(() =>
      screen.getByRole("button", { name: /start extraction/i }),
    );
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);
    await waitFor(() => expect(captureOnEvent).not.toBeNull());
    await screen.findByRole("button", { name: /stop run/i });
    await act(async () => {
      captureOnEvent!({
        event: "status",
        data: { phase: "starting", message: "Starting", run_id: 99 },
        timestamp: Date.now() / 1000,
      });
    });
    expect(window.location.pathname).toMatch(/^\/(?:run|history)\/99$/);
    const staleEvent = captureOnEvent!;
    fireEvent.click(screen.getByRole("link", { name: "Work queue" }));
    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    expect(window.location.pathname).toBe("/");
    act(() => staleEvent({ event: "run_complete", data: { success: true, overall_status: "completed", run_id: 99 }, timestamp: Date.now() / 1000 }));
    expect(window.location.pathname).toBe("/");
    expect(screen.getByRole("heading", { name: "Add documents" })).toBeInTheDocument();
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
        apiFetch: vi.fn(async (url: string, options?: RequestInit) => {
          if (url.startsWith("/api/preparation/")) return preparedSnapshot;
          return actual.apiFetch(url, options);
        }),
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
    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    const file = new File(["x"], "FINCO.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });
    const runButton = await waitFor(() => {
      const btn = screen.queryByRole("button", { name: /start extraction/i });
      if (!btn) throw new Error("Run button not ready");
      expect(btn).toBeEnabled();
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
