import { useEffect, useRef, useState } from "react";
import { getSettings, updateSettings } from "../lib/api";
import { parseThemeOptions, type ClipboardFormatOptions } from "../lib/clipboardFormat";
import { previewNotesAppearance, type PreparedNoteOutput } from "../lib/notesOutput";
import { userMessage } from "../lib/errors";
import { guardNavigationHistory } from "../lib/navigationHistory";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { ClipboardFormatControls } from "./ClipboardFormatControls";
import { ConfirmDialog } from "./ConfirmDialog";
import { PreparedNotesHtml } from "./PreparedNotesHtml";

type Field = keyof ClipboardFormatOptions;
type Patch = Partial<{ [K in Field]: ClipboardFormatOptions[K] | null }>;

export function NotesAppearanceSettings({ onBusyChange }: { onBusyChange?: (busy: boolean) => void }) {
  const [resetOpen, setResetOpen] = useState(false);
  const [house, setHouse] = useState<ClipboardFormatOptions | null>(null);
  const [fmt, setFmt] = useState<ClipboardFormatOptions | null>(null);
  const [overrides, setOverrides] = useState<Partial<ClipboardFormatOptions>>({});
  const [phase, setPhase] = useState<"loading" | "idle" | "pending" | "saving">("loading");
  const [error, setError] = useState<string | null>(null);
  const [output, setOutput] = useState<PreparedNoteOutput | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const confirmed = useRef<{ fmt: ClipboardFormatOptions; overrides: Partial<ClipboardFormatOptions> } | null>(null);
  const pending = useRef<Patch>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    getSettings().then((settings) => {
      if (!mounted.current) return;
      const profile = parseThemeOptions(settings.notes_house_style ?? settings.notes_table_style);
      const effective = parseThemeOptions(settings.notes_table_style);
      const custom = settings.notes_appearance_overrides ?? {};
      setHouse(profile); setFmt(effective); setOverrides(custom);
      confirmed.current = { fmt: effective, overrides: custom }; setPhase("idle");
    }).catch((e) => { if (mounted.current) { setError(userMessage(e)); setPhase("idle"); } });
    return () => { mounted.current = false; if (timer.current) clearTimeout(timer.current); };
  }, []);
  useEffect(() => { onBusyChange?.(phase === "pending" || phase === "saving"); }, [phase, onBusyChange]);
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);
  useEffect(() => {
    if (phase !== "pending" && phase !== "saving") return;
    const unguard = guardNavigationHistory(() => false);
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", unload);
    return () => {
      unguard();
      window.removeEventListener("beforeunload", unload);
    };
  }, [phase]);

  useEffect(() => {
    if (!fmt) return;
    const controller = new AbortController();
    setOutput(null); setPreviewError(null);
    const delay = setTimeout(() => {
      previewNotesAppearance(parseThemeOptions(fmt), controller.signal).then((prepared) => {
        if (!controller.signal.aborted) setOutput(prepared);
      }).catch((e) => { if (!controller.signal.aborted) setPreviewError(userMessage(e)); });
    }, 150);
    return () => { clearTimeout(delay); controller.abort(); };
  }, [fmt]);

  const save = async (patch: Patch, reset = false) => {
    setPhase("saving"); setError(null);
    try {
      await updateSettings(reset ? { notes_appearance_reset: true } : { notes_appearance_overrides: patch });
      const settings = await getSettings();
      if (!mounted.current) return;
      const effective = parseThemeOptions(settings.notes_table_style);
      const custom = settings.notes_appearance_overrides ?? {};
      confirmed.current = { fmt: effective, overrides: custom };
      setFmt(effective); setOverrides(custom);
    } catch (e) {
      if (!mounted.current) return;
      setError(userMessage(e));
      if (confirmed.current) { setFmt(confirmed.current.fmt); setOverrides(confirmed.current.overrides); }
    } finally { if (mounted.current) setPhase("idle"); }
  };
  const schedule = (patch: Patch, next: ClipboardFormatOptions, custom: Partial<ClipboardFormatOptions>) => {
    pending.current = { ...pending.current, ...patch };
    setFmt(next); setOverrides(custom); setPhase("pending");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const edits = pending.current; pending.current = {}; timer.current = null;
      void save(edits);
    }, 500);
  };
  const update = (next: ClipboardFormatOptions) => {
    if (!fmt || !house || phase === "saving") return;
    const clean = parseThemeOptions(next);
    let patch: Patch = {}, custom = { ...overrides };
    for (const key of new Set([...Object.keys(fmt), ...Object.keys(clean)]) as Set<Field>) {
      if (JSON.stringify(fmt[key]) === JSON.stringify(clean[key])) continue;
      patch = { ...patch, [key]: clean[key] ?? null };
      if (clean[key] === undefined) delete custom[key];
      else custom = { ...custom, [key]: clean[key] };
    }
    if (Object.keys(patch).length) schedule(patch, next, custom);
  };
  const resetField = (key: Field) => {
    if (!house) return;
    const custom = { ...overrides }; delete custom[key];
    schedule({ [key]: null }, parseThemeOptions({ ...house, ...custom }), custom);
  };
  const resetAll = () => {
    if (!house || phase === "saving") return;
    if (timer.current) clearTimeout(timer.current);
    pending.current = {}; setResetOpen(false); setFmt(house); setOverrides({}); void save({}, true);
  };

  return <section aria-label="Notes appearance">
    <ConfirmDialog isOpen={resetOpen} title="Reset notes appearance?"
      message="Restore the shared house style, including no table borders or header fill. This removes all shared appearance overrides. Existing run overrides are kept."
      confirmLabel="Reset appearance" onConfirm={resetAll} onCancel={() => setResetOpen(false)} />
    <div style={{ ...ui.sectionHeader, alignItems: "center" }}>
      <h2 style={{ fontSize: 16, margin: 0, fontWeight: pwc.weight.semibold }}>Notes appearance</h2>
      <button type="button" className={uiClass.btnSecondary} style={ui.buttonSecondary}
        disabled={!house || phase === "saving"} onClick={() => setResetOpen(true)}>Reset to house style</button>
    </div>
    {error && <p role="alert" style={{ color: pwc.error }}>{error}</p>}
    {!fmt || !house ? <p role="status">{error ? "Appearance settings could not be loaded." : "Loading appearance…"}</p> : <>
      <div className="notes-appearance-layout" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 32, alignItems: "start" }}>
        <ClipboardFormatControls value={fmt} house={house} overrides={overrides} onChange={update} onReset={resetField}
          idPrefix="settings-notes" disabled={phase === "saving"} />
        <div style={{ minWidth: 0 }}><h3 style={{ fontSize: 14, fontWeight: pwc.weight.semibold, margin: "0 0 12px", minHeight: 21 }}>mTool notes preview</h3>
          <div style={{ border: `1px solid ${pwc.grey300}`, minWidth: 0 }}>
            {output ? <PreparedNotesHtml output={output} /> : <p role={previewError ? "alert" : "status"} style={{ padding: 16 }}>{previewError ?? "Preparing preview…"}</p>}
          </div>
        </div>
      </div>
      <details style={{ marginTop: 24 }}><summary style={{ ...ui.fieldLabel, cursor: "pointer", padding: "8px 0" }}>Formatting applied before export</summary>
        <ul style={{ margin: "8px 0", paddingLeft: 20, fontSize: 14 }}>
          <li>Amount headers and figures align right.</li><li>Double borders become thick single lines.</li>
          <li>Unsized tables fit the page. Merged cells expand for native editing.</li>
          <li>Blank edges and paragraph breaks use mTool-compatible formatting.</li>
          <li>Notes that exceed Excel’s limit show reduced styling or a size warning before export.</li>
        </ul>
      </details>
      <p role="status" aria-live="polite" style={{ color: pwc.grey700, fontSize: 13, minHeight: 20 }}>{phase === "pending" || phase === "saving" ? "Saving…" : "Changes save automatically"}</p>
    </>}
  </section>;
}
