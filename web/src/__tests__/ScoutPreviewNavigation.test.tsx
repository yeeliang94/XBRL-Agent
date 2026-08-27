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

describe("preview scan navigation", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("continues through Settings and restores the completed preview on return", async () => {
    let scoutSignal: AbortSignal | undefined;
    let scoutController: ReadableStreamDefaultController<Uint8Array> | undefined;
    const encoder = new TextEncoder();

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/config") {
        return new Response(JSON.stringify({ canonical_mode: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/scout/preview-session") {
        scoutSignal = init?.signal as AbortSignal;
        return new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              scoutController = controller;
              controller.enqueue(encoder.encode(
                `event: status\ndata: ${JSON.stringify({ phase: "scouting", message: "Reading document" })}\n\n`,
              ));
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        );
      }
      return new Response("Not found", { status: 404 });
    }));

    const { default: App } = await import("../App");
    render(<App />);

    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, {
        target: {
          files: [new File(["pdf"], "Annual-Report.pdf", { type: "application/pdf" })],
        },
      });
    });

    fireEvent.click(await screen.findByTestId("advanced-toggle"));
    fireEvent.click(await screen.findByRole("button", { name: /preview scan/i }));
    await waitFor(() => expect(scoutSignal).toBeDefined());

    fireEvent.click(screen.getByRole("button", { name: /^settings$/i }));
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    const hiddenExtractWorkspace = screen
      .getByText("Annual-Report.pdf")
      .closest("[aria-hidden='true']");
    expect(hiddenExtractWorkspace).toHaveStyle({ display: "none" });
    expect(scoutSignal?.aborted).toBe(false);

    await act(async () => {
      scoutController?.enqueue(encoder.encode(
        `event: scout_complete\ndata: ${JSON.stringify({
          success: true,
          infopack: {
            toc_page: 3,
            page_offset: 0,
            statements: {
              SOFP: {
                variant_suggestion: "CuNonCu",
                face_page: 10,
                note_pages: [],
                confidence: "HIGH",
              },
            },
          },
        })}\n\n`,
      ));
      scoutController?.close();
    });

    act(() => {
      window.history.replaceState({}, "", "/run/99");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    await waitFor(() => {
      const sofpVariant = screen.getAllByRole("combobox").find(
        (element) => element.querySelector("option[value='CuNonCu']"),
      ) as HTMLSelectElement | undefined;
      expect(sofpVariant?.value).toBe("CuNonCu");
    });
  });
});
