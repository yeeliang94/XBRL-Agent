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
  // Canonical mode gate: hide the admin/power-user surfaces when the backend
  // isn't running in canonical mode (peer-review finding 5). Defaults to true
  // so callers that don't yet know the flag keep showing them.
  showConcepts?: boolean;
  // "Field labels" (concepts landing) and "Benchmarks" are power-user/admin
  // surfaces the everyday auditor never needs — gate them on admin so the
  // primary nav stays to Extract + History for most users (Phase 2).
  isAdmin?: boolean;
}

const ITEMS: { id: AppView; label: string; adminOnly?: boolean }[] = [
  { id: "extract", label: "Extract" },
  { id: "history", label: "History" },
  // The concept-label editor: renamed from "Template" (which an auditor read
  // as the MBRS Excel template) to "Field labels", and admin-only.
  { id: "concepts", label: "Field labels", adminOnly: true },
  // Gold-standard eval (v16): the benchmark library — an internal QA feature,
  // so admin-only too.
  { id: "benchmarks", label: "Benchmarks", adminOnly: true },
  // Evals workspace (Phase E/F): suites, batch runner, trends + compare. Shares
  // the QA-surface admin gate with Benchmarks (which it depends on for gold).
  { id: "suites", label: "Evals", adminOnly: true },
];

export function TopNav({ view, onViewChange, showConcepts = true, isAdmin = false }: TopNavProps) {
  // Both the Field-labels (concepts) and Benchmarks tabs are canonical-mode
  // surfaces AND admin-only, so they share both gates.
  const items = ITEMS.filter((i) => {
    if (i.adminOnly && (!showConcepts || !isAdmin)) return false;
    return true;
  });
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
