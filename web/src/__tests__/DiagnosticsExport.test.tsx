import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DiagnosticsExport } from "../components/DiagnosticsExport";

// Exercise the real download helper; only browser file-saving and HTTP are substituted.
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("downloads the selected run ZIP and shows preparation while waiting", async () => {
  let finish!: (response: Response) => void;
  const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; }));
  vi.stubGlobal("fetch", fetchMock);
  const createObjectURL = vi.fn(() => "blob:diagnostics");
  vi.stubGlobal("URL", class extends URL { static createObjectURL = createObjectURL; static revokeObjectURL = vi.fn(); });
  let savedName = "";
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { savedName = this.download; });
  render(<DiagnosticsExport runId={42} />);
  fireEvent.click(screen.getByRole("button", { name: "Export diagnostics" }));
  expect(screen.getByRole("button", { name: "Preparing diagnostics…" })).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledWith("/api/runs/42/diagnostics");
  finish(new Response("zip", { headers: { "Content-Type": "application/zip" } }));
  await waitFor(() => expect(savedName).toBe("run-42-diagnostics.zip"));
  expect(createObjectURL).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Export diagnostics" })).toBeEnabled();
  expect(document.querySelector('a[download]')).toBeNull();
});

test("shows download errors, allows retry and handles expired sessions", async () => {
  const expired = vi.fn();
  window.addEventListener("auth:unauthorized", expired);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Not authenticated." }), { status: 401 })));
  render(<DiagnosticsExport runId={7} />);
  fireEvent.click(screen.getByRole("button", { name: "Export diagnostics" }));
  await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
  expect(screen.getByRole("button", { name: "Export diagnostics" })).toBeEnabled();
  expect(expired).toHaveBeenCalledOnce();
  window.removeEventListener("auth:unauthorized", expired);
});
