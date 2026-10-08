import { beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { NotesAppearanceSettings } from "../components/NotesAppearanceSettings";
import { getSettings, updateSettings } from "../lib/api";
import { previewNotesAppearance } from "../lib/notesOutput";
import { ui } from "../lib/uiStyles";
import type { ClipboardFormatOptions } from "../lib/clipboardFormat";

vi.mock("../lib/api", () => ({ getSettings: vi.fn(), updateSettings: vi.fn() }));
vi.mock("../lib/notesOutput", async () => ({
  ...await vi.importActual("../lib/notesOutput"), previewNotesAppearance: vi.fn(),
}));
const house: ClipboardFormatOptions = { borderStyle: "none", fontSizePt: 11, cellPaddingPx: [3, 3], paragraphSpacingPx: 3, headerBold: true, headerFill: "transparent", headerRule: false, totalsDoubleUnderline: false };
let custom: Partial<ClipboardFormatOptions>;
beforeEach(() => {
  vi.clearAllMocks(); custom = {};
  vi.mocked(getSettings).mockImplementation(async () => ({ model: "test", proxy_url: "", api_key_set: false, api_key_preview: "", notes_house_style: house, notes_appearance_overrides: custom, notes_table_style: { ...house, ...custom } }));
  vi.mocked(updateSettings).mockImplementation(async (body) => {
    if (body.notes_appearance_reset) custom = {};
    for (const [key, value] of Object.entries(body.notes_appearance_overrides ?? {})) {
      if (value === null) delete custom[key as keyof ClipboardFormatOptions];
      else custom = { ...custom, [key]: value };
    }
    return { status: "ok" };
  });
  vi.mocked(previewNotesAppearance).mockResolvedValue({ html: '<p style="font-family:Arial">Prepared mTool sample</p>', tier: "full", revision: "sample", source_styling_dropped: false, white_grid_dropped: false });
});

describe("Notes appearance", () => {
  test("shows exporter-prepared sample and shared design-system controls", async () => {
    render(<NotesAppearanceSettings />);
    expect(await screen.findByText("Prepared mTool sample")).toBeInTheDocument();
    expect(screen.getByLabelText("Table border").style.height).toBe(`${ui.select.height}px`);
    expect(screen.getByLabelText("Font size (pt)").style.height).toBe(`${ui.input.height}px`);
    expect(screen.getByLabelText("Font size (pt)")).toHaveValue(11);
    expect(screen.getByLabelText("Paragraph gap (px)")).toHaveValue(3);
    expect(screen.getByLabelText("Vertical padding (px)")).toHaveValue(3);
    expect(screen.getByLabelText("Horizontal padding (px)")).toHaveValue(3);
    expect(screen.getByLabelText("Table border")).toHaveValue("none");
    expect(screen.getByLabelText("Header fill")).toHaveValue("transparent");
    expect(screen.getByLabelText("Header emphasis")).toHaveValue("true");
    expect(screen.queryByText("Custom")).toBeNull();
    expect(screen.getByText("Formatting applied before export").closest("details")).not.toHaveAttribute("open");
  });
  test("saves only changed fields and keeps navigation busy until confirmed", async () => {
    const busy = vi.fn(); render(<NotesAppearanceSettings onBusyChange={busy} />);
    const font = await screen.findByLabelText("Font size (pt)");
    fireEvent.change(font, { target: { value: "1" } });
    fireEvent.change(font, { target: { value: "12" } });
    expect(font).toHaveValue(12);
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ notes_appearance_overrides: { fontSizePt: 12 } }));
    await waitFor(() => expect(busy).toHaveBeenLastCalledWith(false));
    expect(busy).toHaveBeenCalledWith(true);
    expect(screen.getByRole("button", { name: "Reset Font size (pt)" })).toBeInTheDocument();
  });
  test.each(["transparent", "#f4f4f4", "#e6eef6"])("custom header colour from %s saves and reaches the prepared preview", async (headerFill) => {
    custom = { headerFill };
    render(<NotesAppearanceSettings />);
    const fill = await screen.findByLabelText("Header fill");
    expect(screen.getByLabelText("Custom fill colour")).toBeDisabled();
    fireEvent.change(fill, { target: { value: "custom" } });
    const picker = screen.getByLabelText("Custom fill colour");
    expect(picker).toBeEnabled();
    expect(fill).toHaveValue("custom");
    fireEvent.input(picker, { target: { value: "#f4f4f4" } });
    expect(picker).toBeEnabled();
    expect(fill).toHaveValue("custom");
    fireEvent.input(picker, { target: { value: "#dbeafe" } });
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ notes_appearance_overrides: { headerFill: "#dbeafe" } }));
    await waitFor(() => expect(previewNotesAppearance).toHaveBeenLastCalledWith(expect.objectContaining({ headerFill: "#dbeafe" }), expect.any(AbortSignal)));
    fireEvent.change(fill, { target: { value: "transparent" } });
    await waitFor(() => expect(updateSettings).toHaveBeenLastCalledWith({ notes_appearance_overrides: { headerFill: "transparent" } }));
  });
  test("clamps numeric values before saving", async () => {
    render(<NotesAppearanceSettings />);
    fireEvent.change(await screen.findByLabelText("Font size (pt)"), { target: { value: "99" } });
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ notes_appearance_overrides: { fontSizePt: 24 } }));
    await waitFor(() => expect(screen.getByLabelText("Font size (pt)")).toHaveValue(24));
  });
  test("field reset removes its override and whole reset removes all overrides", async () => {
    custom = { fontSizePt: 12, borderStyle: "single" };
    render(<NotesAppearanceSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Reset Font size (pt)" }));
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ notes_appearance_overrides: { fontSizePt: null } }));
    await waitFor(() => expect(screen.getByLabelText("Font size (pt)")).toHaveValue(11));
    expect(screen.getByLabelText("Table border")).toHaveValue("single");
    fireEvent.click(screen.getByRole("button", { name: "Reset to house style" }));
    expect(updateSettings).not.toHaveBeenCalledWith({ notes_appearance_reset: true });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByLabelText("Table border")).toHaveValue("single");
    fireEvent.click(screen.getByRole("button", { name: "Reset to house style" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset appearance" }));
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ notes_appearance_reset: true }));
    await waitFor(() => expect(screen.queryByText("Custom")).toBeNull());
    expect(screen.getByLabelText("Table border")).toHaveValue("none");
  });
  test("reset confirmation waits for an appearance save already in progress", async () => {
    let finishSave!: (value: { status: string }) => void;
    vi.mocked(updateSettings).mockImplementationOnce(() => new Promise((resolve) => { finishSave = resolve; }));
    render(<NotesAppearanceSettings />);
    const font = await screen.findByLabelText("Font size (pt)");
    vi.useFakeTimers();
    try {
      fireEvent.change(font, { target: { value: "12" } });
      fireEvent.click(screen.getByRole("button", { name: "Reset to house style" }));
      const dialog = screen.getByRole("dialog", { name: "Reset notes appearance?" });
      await act(() => vi.advanceTimersByTimeAsync(500));
      const busyButton = within(dialog).getByRole("button", { name: "Saving…" });
      expect(busyButton).toBeDisabled();
      fireEvent.click(busyButton);
      expect(updateSettings).toHaveBeenCalledTimes(1);
      await act(async () => { finishSave({ status: "ok" }); });
      const reset = within(dialog).getByRole("button", { name: "Reset appearance" });
      expect(reset).toBeEnabled();
      await act(async () => { fireEvent.click(reset); });
      expect(updateSettings).toHaveBeenLastCalledWith({ notes_appearance_reset: true });
      expect(screen.queryByRole("dialog")).toBeNull();
    } finally { vi.useRealTimers(); }
  });
  test("failed save restores confirmed settings and reports failure", async () => {
    vi.mocked(updateSettings).mockRejectedValue(new Error("Save unavailable"));
    render(<NotesAppearanceSettings />);
    fireEvent.change(await screen.findByLabelText("Table border"), { target: { value: "single" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Save unavailable");
    expect(screen.getByLabelText("Table border")).toHaveValue("none");
  });
});
