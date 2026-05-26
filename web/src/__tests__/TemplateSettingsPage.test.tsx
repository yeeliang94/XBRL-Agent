import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { TemplateSettingsPage } from "../pages/TemplateSettingsPage";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const templates = { templates: [{ template_id: "mfrs-company-sofp-cunoncu-v1", shape: "linear" }] };
const concepts = {
  template_id: "mfrs-company-sofp-cunoncu-v1",
  concepts: [
    {
      concept_uuid: "leaf-1",
      parent_uuid: null,
      kind: "LEAF",
      canonical_label: "Biological assets",
      display_label: null,
      render_sheet: "SOFP-CuNonCu",
      render_row: 10,
      render_col: "B",
      matrix_col: null,
    },
    {
      concept_uuid: "abs-1",
      parent_uuid: null,
      kind: "ABSTRACT",
      canonical_label: "Non-current assets",
      display_label: null,
      render_sheet: "SOFP-CuNonCu",
      render_row: 7,
      render_col: "B",
      matrix_col: null,
    },
  ],
};

describe("TemplateSettingsPage", () => {
  test("lists templates and renders their concept labels (no values)", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string) => {
        const body = url.includes("/concepts") ? concepts : templates;
        return { ok: true, status: 200, json: async () => body } as Response;
      }
    );
    render(<TemplateSettingsPage />);
    await waitFor(() => screen.getByTestId("ts-row-leaf-1"));
    expect(screen.getByText("Biological assets")).toBeTruthy();
    // ABSTRACT rows are not renamable.
    expect(screen.queryByTestId("ts-rename-btn-abs-1")).toBeNull();
    expect(screen.getByTestId("ts-rename-btn-leaf-1")).toBeTruthy();
  });

  test("renaming a label PATCHes the global display_label endpoint", async () => {
    const patches: Array<{ url: string; body: any }> = [];
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        if (init?.method === "PATCH") {
          patches.push({ url, body: JSON.parse(init.body as string) });
          return { ok: true, status: 200, json: async () => ({}) } as Response;
        }
        const body = url.includes("/concepts") ? concepts : templates;
        return { ok: true, status: 200, json: async () => body } as Response;
      }
    );
    render(<TemplateSettingsPage />);
    const btn = await waitFor(() => screen.getByTestId("ts-rename-btn-leaf-1"));
    fireEvent.click(btn);
    const input = screen.getByTestId("ts-rename-input-leaf-1") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Livestock" } });
    fireEvent.blur(input);
    await waitFor(() =>
      expect(patches.find((p) => p.url.includes("/api/concepts/leaf-1/display_label"))).toBeTruthy()
    );
    const p = patches.find((x) => x.url.includes("/api/concepts/leaf-1/display_label"))!;
    expect(p.body.display_label).toBe("Livestock");
  });
});
