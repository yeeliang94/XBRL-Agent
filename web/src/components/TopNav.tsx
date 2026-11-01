import type { AppView } from "../lib/appReducer";
import { ds, tokens } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { Description, Flag, RunHistory, UploadFile } from "./iconGlyphs";

export function TopNav({ view, extractMode = "queue", hasDocument = false, onViewChange, onAdd, onIssues }: {
  view: AppView;
  extractMode?: "queue" | "new";
  hasDocument?: boolean;
  onViewChange: (view: AppView) => void;
  onAdd?: () => void;
  /** Admins only: the Issues page. No count or dot; the page shows New. */
  onIssues?: () => void;
}) {
  const destinations = [
    { label: "Work queue", href: "/", glyph: Description, active: view === "extract" && extractMode === "queue" && !hasDocument, action: () => onViewChange("extract") },
    { label: "Add documents", href: "/#new-extraction", glyph: UploadFile, active: view === "extract" && extractMode === "new" && !hasDocument, action: onAdd },
    { label: "History", href: "/history", glyph: RunHistory, active: view === "history" && !hasDocument, action: () => onViewChange("history") },
    ...(onIssues ? [{ label: "Issues", href: "/issues", glyph: Flag, active: view === "issues", action: onIssues }] : []),
  ];
  return <nav id="app-primary-navigation" className="app-primary-navigation" aria-label="Main navigation"
    style={{ display: "flex", flexDirection: "column", gap: 4 }}>
    {destinations.map(({ label, href, glyph: Glyph, active, action }) => <a key={href} href={href}
      aria-label={label} data-tooltip={label} aria-current={active ? "page" : undefined}
      className="app-btn-quiet app-navigation-link"
      style={{ ...ui.navLink, ...(active ? ui.navLinkActive : {}) }}
      onClick={(event) => {
        if (!action || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        action();
      }}>
      <span aria-hidden="true" style={{ display: "inline-flex", flexShrink: 0, color: active ? ds.orange500 : tokens.color.icon.rest }}><Glyph size={20} /></span>
      <span className="app-navigation-label">{label}</span>
    </a>)}
  </nav>;
}
