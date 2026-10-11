import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  const settings = {
    model: "gemini-3-flash",
    proxy_url: "",
    api_key_set: true,
    api_key_preview: "",
    available_models: [
      {
        id: "gemini-3-flash",
        display_name: "Gemini 3 Flash",
        provider: "google",
        supports_vision: true,
        notes: "",
      },
    ],
    default_models: {
      scout: "gemini-3-flash",
      SOFP: "gemini-3-flash",
      SOPL: "gemini-3-flash",
      SOCI: "gemini-3-flash",
      SOCF: "gemini-3-flash",
      SOCIE: "gemini-3-flash",
    },
    tolerance_rm: 1,
    auto_review: true,
  };
  return {
    ...actual,
    getAuthMe: vi.fn(async () => ({
      email: "dev@localhost",
      display_name: "Dev",
      provider: "dev",
      is_admin: false,
    })),
    getSettings: vi.fn(async () => settings),
    getExtendedSettings: vi.fn(async () => settings),
    fetchRuns: vi.fn(async () => ({ runs: [], total: 0 })),
    uploadPdf: vi.fn(async () => ({
      session_id: "preview-session",
      filename: "Annual-Report.pdf",
      run_id: 99,
    })),
    patchRunConfig: vi.fn(async () => ({ status: "ok" })),
  };
});

describe("preparation navigation", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("preparation continues through Settings and setup opens with the detected format on return", async () => {
    const working = {
      attempt_id: "a1", status: "working", stage: "capture", phase: "preparing_pages",
      action_required: "none", message: "Reading pages", total: 4, captured: 1, checked: 0,
    };
    let snapshot: Record<string, unknown> = working;

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/config") {
        return new Response(JSON.stringify({ canonical_mode: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/preparation/preview-session") {
        return new Response(JSON.stringify(snapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("Not found", { status: 404 });
    }));

    const { default: App } = await import("../App");
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, {
        target: {
          files: [new File(["pdf"], "Annual-Report.pdf", { type: "application/pdf" })],
        },
      });
    });

    expect(await screen.findByRole("list", { name: "Live activity" })).toHaveTextContent("Reading pages");
    expect(screen.queryByRole("heading", { name: "Filing setup" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^settings$/i }));
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    const hiddenExtractWorkspace = screen
      .getByText("Annual-Report.pdf")
      .closest("[aria-hidden='true']");
    expect(hiddenExtractWorkspace).toHaveStyle({ display: "none" });

    // Preparation finishes while the operator is in Settings.
    snapshot = {
      ...working, status: "succeeded", stage: "ready", phase: "awaiting_confirmation",
      action_required: "confirm_setup", prepared: true, captured: 4, checked: 4,
      message: "Document prepared",
      infopack: {
        toc_page: 3,
        page_offset: 0,
        scale_unit: "thousands",
        notes_inventory: [],
        statements: {
          SOFP: { variant_suggestion: "CuNonCu", face_page: 10, note_pages: [], confidence: "HIGH" },
        },
      },
    };

    act(() => {
      window.history.replaceState({}, "", "/run/99");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    // Setup opens after the next poll and the brief prepared hold.
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Filing setup" })).toBeInTheDocument();
      const sofpVariant = screen.getAllByRole("combobox").find(
        (element) => element.querySelector("option[value='CuNonCu']"),
      ) as HTMLSelectElement | undefined;
      expect(sofpVariant?.value).toBe("CuNonCu");
    }, { timeout: 6000 });
  }, 10_000);
});
