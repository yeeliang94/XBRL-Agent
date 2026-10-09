import { useEffect, useState } from "react";
import { getAgentInstructions, getInstructionSources, saveAgentInstructions } from "../lib/api";
import type { InstructionSourceGroup, TeamGuidanceSettings } from "../lib/types";
import { ui, uiClass } from "../lib/uiStyles";
import { pwc, tokens } from "../lib/theme";
import { ApiError } from "../lib/errors";
import { guardNavigationHistory } from "../lib/navigationHistory";

const recipients: Record<string, string> = {
  all: "Figures extraction, Figures review, Notes extraction, Notes review",
  figures: "Figures extraction, Figures review", notes: "Notes extraction, Notes review",
  figures_extraction: "Figures extraction", figures_review: "Figures review",
  notes_extraction: "Notes extraction", notes_review: "Notes review",
};

export function AgentInstructionsPanel({ isAdmin, onDirtyChange }: {
  isAdmin: boolean; onDirtyChange: (dirty: boolean) => void;
}) {
  const [saved, setSaved] = useState<TeamGuidanceSettings | null>(null);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [scope, setScope] = useState("figures");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState("");
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const dirty = saved !== null && Object.keys(texts).some(key => texts[key] !== saved.texts[key]);
  const load = async () => {
    setError("");
    try { const data = await getAgentInstructions(); setSaved(data); setTexts(data.texts); setConflict(false); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load guidance."); }
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    onDirtyChange(dirty);
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    const navigate = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const link = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
      const destination = new URL(link.href, window.location.href);
      if (destination.origin === window.location.origin && destination.pathname === window.location.pathname && destination.search === window.location.search) return;
      if (link && !window.confirm("Discard unsaved guidance?")) { event.preventDefault(); event.stopPropagation(); }
    };
    const unguard = guardNavigationHistory(() => window.confirm("Discard unsaved guidance?"));
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", navigate, true);
    return () => { unguard(); window.removeEventListener("beforeunload", unload); document.removeEventListener("click", navigate, true); };
  }, [dirty, onDirtyChange]);
  const save = async () => {
    if (!saved || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const data = await saveAgentInstructions({ texts, revision: saved.revision });
      setSaved(data); setTexts(data.texts); setConflict(false); setNotice("Guidance saved. Applies to new runs.");
    } catch (e) {
      const stale = e instanceof ApiError && e.status === 409;
      setConflict(stale);
      setError(stale ? "Someone else saved guidance. Your changes are still here. Reload the saved version before editing again." : e instanceof Error ? e.message : "Could not save guidance.");
    } finally { setBusy(false); }
  };

  if (!saved) return <div style={styles.stack}>
    {error ? <><p role="alert">{error}</p><button type="button" style={ui.buttonSecondary} onClick={() => void load()}>Retry</button></> : <p role="status">Loading instructions…</p>}
  </div>;
  return <div style={styles.stack}>
    <h2 style={styles.heading}>Team guidance</h2>
    <div style={styles.field}>
      <label htmlFor="guidance-scope" style={ui.fieldLabel}>Applies to</label>
      <select id="guidance-scope" style={ui.select} value={scope} disabled={busy} onChange={e => {
        if (dirty && !window.confirm("Discard unsaved guidance?")) return;
        setTexts(saved.texts); setScope(e.target.value); setError(""); setNotice("");
      }}>
        {Object.entries(saved.scopes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
      <p style={styles.meta}>Recipients: {recipients[scope]}</p>
    </div>
    <div style={styles.field}>
      <label htmlFor="team-guidance" style={ui.fieldLabel}>Additional instructions</label>
      <textarea id="team-guidance" style={styles.textarea} rows={8} readOnly={!isAdmin} disabled={busy}
        maxLength={saved.max_length} value={texts[scope] ?? ""} aria-describedby="guidance-help"
        placeholder="For [statement and situation], use [intended concept] when [condition]. Otherwise follow the normal mapping instructions."
        onChange={e => { setTexts({ ...texts, [scope]: e.target.value }); setNotice(""); }} />
      <p id="guidance-help" style={styles.meta}>Describe the situation, expected result, and exceptions. Guidance applies to new runs and must follow source and template rules.</p>
      {!isAdmin && <p style={styles.meta}>An administrator can edit team guidance.</p>}
    </div>
    <div style={styles.actions}>
      <span style={styles.meta}>{saved.updated_at ? `Last saved by ${saved.updated_by}, ${new Date(saved.updated_at).toLocaleString()}` : "No team guidance saved yet."}</span>
      {isAdmin && <button type="button" style={ui.buttonPrimary} className={uiClass.btnPrimary}
        disabled={!dirty || busy || conflict} onClick={() => void save()}>{busy ? "Saving…" : "Save guidance"}</button>}
    </div>
    {error && <div role="alert" style={styles.field}><p style={{ margin: 0 }}>{error}</p>
      {conflict && <button type="button" style={ui.buttonSecondary} onClick={() => {
        if (window.confirm("Discard your unsaved changes and reload saved guidance?")) void load();
      }}>Reload saved guidance</button>}
    </div>}
    {notice && <p role="status" style={styles.meta}>{notice}</p>}
    <details onToggle={e => setSourcesOpen(e.currentTarget.open)}>
      <summary style={styles.summary}>View application instructions</summary>
      {sourcesOpen && <InstructionSources />}
    </details>
  </div>;
}

function InstructionSources() {
  const [groups, setGroups] = useState<InstructionSourceGroup[] | null>(null);
  const [role, setRole] = useState("Figures extraction");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const load = async () => {
    setError("");
    try { setGroups(await getInstructionSources()); } catch (e) { setError(e instanceof Error ? e.message : "Could not load instruction sources."); }
  };
  useEffect(() => { void load(); }, []);
  const group = groups?.find(item => item.role === role);
  const source = group?.sources.find(item => item.name === name) ?? group?.sources[0];
  return <div style={{ ...styles.stack, marginTop: pwc.space.lg }}>
    <h3 style={styles.heading}>Application instruction sources</h3>
    <p style={styles.meta}>Read-only source components. Each run also adds its filing context, source information, and team guidance.</p>
    {error && <><p role="alert">{error}</p><button type="button" style={ui.buttonSecondary} onClick={() => void load()}>Retry sources</button></>}
    {!groups && !error && <p role="status">Loading instruction sources…</p>}
    {groups && <>
      <div style={styles.field}><label htmlFor="source-agent" style={ui.fieldLabel}>Agent</label>
        <select id="source-agent" style={ui.select} value={role} onChange={e => { setRole(e.target.value); setName(""); }}>
          {groups.map(item => <option key={item.role}>{item.role}</option>)}
        </select></div>
      <div style={styles.field}><label htmlFor="source-component" style={ui.fieldLabel}>Instruction component</label>
        <select id="source-component" style={ui.select} value={source?.name} onChange={e => setName(e.target.value)}>
          {group?.sources.map(item => <option key={item.name} value={item.name}>{item.label ?? item.name}</option>)}
        </select></div>
      <pre style={styles.source}>{source?.text}</pre>
    </>}
  </div>;
}

const styles = {
  stack: { display: "grid", gap: pwc.space.lg, minWidth: 0 } as React.CSSProperties,
  field: { display: "grid", gap: pwc.space.sm } as React.CSSProperties,
  heading: { margin: 0, fontSize: 16, fontWeight: pwc.weight.semibold },
  meta: { margin: 0, fontSize: 13, color: tokens.color.text.secondary },
  textarea: { ...ui.textarea, minHeight: 180, width: "100%", lineHeight: 1.5 } as React.CSSProperties,
  actions: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: pwc.space.md, flexWrap: "wrap" } as React.CSSProperties,
  summary: { cursor: "pointer", fontSize: 14, fontWeight: pwc.weight.medium },
  source: { margin: 0, padding: 16, border: `1px solid ${pwc.grey200}`, borderRadius: 8, fontFamily: pwc.fontBody, fontSize: 14, lineHeight: 1.5, whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 480, overflow: "auto" } as React.CSSProperties,
};
