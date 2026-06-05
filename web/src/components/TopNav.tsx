import { pwc } from "../lib/theme";
import type { AppView } from "../lib/appReducer";

// ---------------------------------------------------------------------------
// TopNav — SPA-style top navigation with top-level app destinations.
//
// Inline styles (per CLAUDE.md #7: Tailwind didn't load reliably on Windows,
// so the entire frontend uses inline style props). Uses ARIA role="tablist"
// to keep the buttons discoverable via the tab role for tests and AT users.
// ---------------------------------------------------------------------------

export interface TopNavProps {
  view: AppView;
  onViewChange: (view: AppView) => void;
  // Canonical mode gate: hide the Concepts tab when the backend isn't
  // running in canonical mode (peer-review finding 5). Defaults to true so
  // callers that don't yet know the flag keep showing it.
  showConcepts?: boolean;
}

const ITEMS: { id: AppView; label: string }[] = [
  { id: "extract", label: "Extract" },
  { id: "history", label: "History" },
  { id: "concepts", label: "Template" },
  // Gold-standard eval (v16): the benchmark library. Gated behind the same
  // canonical-mode flag as Template (eval is built on the canonical store).
  { id: "benchmarks", label: "Benchmarks" },
];

export function TopNav({ view, onViewChange, showConcepts = true }: TopNavProps) {
  // Both the Template (concepts) and Benchmarks tabs are canonical-mode
  // surfaces, so they share the same gate.
  const items = showConcepts
    ? ITEMS
    : ITEMS.filter((i) => i.id !== "concepts" && i.id !== "benchmarks");
  return (
    <nav style={styles.nav} role="tablist" aria-label="Main navigation">
      {items.map((item) => {
        const active = item.id === view;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onViewChange(item.id)}
            // Active and inactive buttons intentionally have distinct style
            // objects so tests can detect the visual difference without
            // hard-coding specific CSS properties.
            style={active ? styles.tabActive : styles.tabInactive}
          >
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const tabBase: React.CSSProperties = {
  padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
  fontFamily: pwc.fontHeading,
  fontSize: 15,
  fontWeight: pwc.weight.medium,
  background: "none",
  border: "none",
  borderBottom: "2px solid transparent",
  cursor: "pointer",
  outline: "none",
};

const styles = {
  nav: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  tabActive: {
    ...tabBase,
    color: pwc.orange500,
    borderBottom: `2px solid ${pwc.orange500}`,
  } as React.CSSProperties,
  tabInactive: {
    ...tabBase,
    color: pwc.grey700,
  } as React.CSSProperties,
} as const;
