import { pwc } from "../lib/theme";
import type { AppView } from "../App";

// ---------------------------------------------------------------------------
// TopNav — SPA-style top navigation with two buttons: Extract and History.
//
// Inline styles (per CLAUDE.md #7: Tailwind didn't load reliably on Windows,
// so the entire frontend uses inline style props). Uses ARIA role="tablist"
// to keep the buttons discoverable via the tab role for tests and AT users.
// ---------------------------------------------------------------------------

export interface TopNavProps {
  view: AppView;
  onViewChange: (view: AppView) => void;
}

const ITEMS: { id: AppView; label: string }[] = [
  { id: "extract", label: "Extract" },
  { id: "history", label: "History" },
];

export function TopNav({ view, onViewChange }: TopNavProps) {
  return (
    <nav style={styles.nav} role="tablist" aria-label="Main navigation">
      {ITEMS.map((item) => {
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
  fontSize: 14,
  fontWeight: 600,
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
