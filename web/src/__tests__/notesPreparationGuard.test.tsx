import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { RunDetailView } from "../components/RunDetailView";
import { RUN_TAB_CHANGE_EVENT, writeRunTabToUrl } from "../lib/runTabs";
import type { RunDetailJson } from "../lib/types";

vi.mock("../pages/ConceptsPage", () => ({
  ConceptsPage: ({ onPreparationBlocked }: { onPreparationBlocked: (blocked: boolean) => void }) => (
    <div>
      <button onClick={() => onPreparationBlocked(true)}>Pending note edit</button>
      <button onClick={() => onPreparationBlocked(false)}>Note saved</button>
    </div>
  ),
}));
vi.mock("../components/MtoolFillModal", () => ({
  MtoolFillModal: ({ open }: { open: boolean }) => open ? <div role="dialog">Prepare saved notes</div> : null,
}));
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

test("preparation and run navigation wait for note saves, then export is available", () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
  const detail: RunDetailJson = {
    id: 42, created_at: "2026-09-17T10:00:00Z", pdf_filename: "notes.pdf",
    status: "completed", session_id: "s", output_dir: "/tmp/s",
    merged_workbook_path: "/tmp/s/filled.xlsx", scout_enabled: true,
    started_at: "2026-09-17T10:00:00Z", ended_at: "2026-09-17T10:01:00Z",
    config: { statements: [], notes_to_run: ["CORP_INFO"] }, agents: [], cross_checks: [],
  };
  render(<RunDetailView detail={detail} initialTab="notes" onDelete={vi.fn()} />);
  const prepare = screen.getByRole("button", { name: "Download draft" });
  const overview = within(screen.getByRole("tablist", { name: "Run detail sections" })).getByRole("tab", { name: "Overview" });
  fireEvent.click(screen.getByRole("button", { name: "Pending note edit" }));
  expect(prepare).toBeDisabled();
  expect(overview).toBeDisabled();
  window.history.pushState({}, "", "/run/42?tab=overview");
  fireEvent.popState(window);
  expect(new URLSearchParams(window.location.search).get("tab")).toBe("notes");
  expect(within(screen.getByRole("tablist", { name: "Run detail sections" })).getByRole("tab", { name: "Notes" })).toHaveAttribute("aria-selected", "true");
  const tabListener = vi.fn();
  window.addEventListener(RUN_TAB_CHANGE_EVENT, tabListener);
  act(() => writeRunTabToUrl("overview"));
  expect(new URLSearchParams(window.location.search).get("tab")).toBe("notes");
  expect(tabListener).toHaveBeenCalledTimes(1);
  expect((tabListener.mock.calls[0][0] as CustomEvent).detail).toBe("notes");
  window.removeEventListener(RUN_TAB_CHANGE_EVENT, tabListener);
  fireEvent.click(prepare);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Note saved" }));
  expect(prepare).toBeEnabled();
  expect(overview).toBeEnabled();
  fireEvent.click(prepare);
  expect(screen.getByRole("dialog")).toHaveTextContent("Prepare saved notes");
});
