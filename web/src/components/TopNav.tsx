import type { AppView } from "../lib/appReducer";
import { pwc, tokens } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { Description, RunHistory, UploadFile } from "./iconGlyphs";

export function TopNav({ view, extractMode = "queue", hasDocument = false, onViewChange, onAdd }: {
  view: AppView;
  extractMode?: "queue" | "new";
  hasDocument?: boolean;
  onViewChange: (view: AppView) => void;
  onAdd?: () => void;
}) {
  const destinations = [
    { label: "Work queue", href: "/", glyph: Description, active: view === "extract" && extractMode === "queue" && !hasDocument, action: () => onViewChange("extract") },
    { label: "Add documents", href: "/#new-extraction", glyph: UploadFile, active: view === "extract" && extractMode === "new" && !hasDocument, action: onAdd },
    { label: "History", href: "/history", glyph: RunHistory, active: view === "history" && !hasDocument, action: () => onViewChange("history") },
  ];
  return <nav id="app-primary-navigation" className="app-primary-navigation" aria-label="Main navigation"
    style={{ display: "flex", flexDirection: "column", gap: 4 }}>
    {destinations.map(({ label, href, glyph: Glyph, active, action }) => <a key={href} href={href}
      aria-label={label} data-tooltip={label} aria-current={active ? "page" : undefined}
      className="pwc-btn-quiet app-navigation-link"
      style={{ ...ui.navLink, ...(active ? ui.navLinkActive : {}) }}
      onClick={(event) => {
        if (!action || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        action();
      }}>
      <span aria-hidden="true" style={{ display: "inline-flex", flexShrink: 0, color: active ? pwc.orange500 : tokens.color.icon.rest }}><Glyph size={20} /></span>
      <span className="app-navigation-label">{label}</span>
    </a>)}
  </nav>;
}
