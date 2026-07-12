import type { MouseEvent } from "react";
import { pwc, tokens, component } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import type { AppView } from "../lib/appReducer";
import { TERMS } from "../lib/vocabulary";

// ---------------------------------------------------------------------------
// TopNav — top-level app destinations.
//
// Destinations are LINKS with stable URLs and aria-current (design-system
// Tabs & navigation: the ARIA tab pattern is reserved for alternate views of
// one resource, e.g. run-detail sections). Left-clicks stay SPA navigations
// via onViewChange; modified clicks (cmd/ctrl/shift/alt) and middle clicks
// fall through to the browser so open-in-new-tab keeps working.
//
// Inline styles (per CLAUDE.md #7). The active destination is dark readable
// text plus the signature-orange indicator — orange is never small nav text.
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

// Stable URL per destination — must agree with parseRouteFromPath /
// the URL-sync effect in App.tsx.
const ITEMS: { id: AppView; label: string; href: string; adminOnly?: boolean }[] = [
  { id: "extract", label: TERMS.newExtraction, href: "/" },
  { id: "history", label: TERMS.runs, href: "/history" },
  // The concept-label editor: renamed from "Template" (which an auditor read
  // as the MBRS Excel template) to "Field labels", and admin-only.
  { id: "concepts", label: "Field labels", href: "/field-labels", adminOnly: true },
  // Gold-standard eval (v16): the benchmark library — an internal QA feature,
  // so admin-only too.
  { id: "benchmarks", label: "Benchmarks", href: "/benchmarks", adminOnly: true },
  // Evals workspace (Phase E/F): suites, batch runner, trends + compare. Shares
  // the QA-surface admin gate with Benchmarks (which it depends on for gold).
  { id: "suites", label: TERMS.evaluationSuites, href: "/evals", adminOnly: true },
];

export function TopNav({ view, onViewChange, showConcepts = true, isAdmin = false }: TopNavProps) {
  // Both the Field-labels (concepts) and Benchmarks tabs are canonical-mode
  // surfaces AND admin-only, so they share both gates.
  const items = ITEMS.filter((i) => {
    if (i.adminOnly && (!showConcepts || !isAdmin)) return false;
    return true;
  });
  const handleClick = (event: MouseEvent<HTMLAnchorElement>, id: AppView) => {
    // Let the browser handle modified/middle clicks (new tab / new window).
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return;
    }
    event.preventDefault();
    onViewChange(id);
  };
  return (
    <nav className="app-main-nav" style={styles.nav} aria-label="Main navigation">
      {items.map((item) => {
        const active = item.id === view;
        return (
          <a
            key={item.id}
            href={item.href}
            aria-current={active ? "page" : undefined}
            onClick={(event) => handleClick(event, item.id)}
            className="app-main-nav-tab"
            // Active and inactive links intentionally have distinct style
            // objects so tests can detect the visual difference without
            // hard-coding specific CSS properties.
            style={active ? styles.tabActive : styles.tabInactive}
          >
            {item.label}
          </a>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Styles — shared underline-tab geometry from ui.tab / ui.tabActive.
// ---------------------------------------------------------------------------

const styles = {
  nav: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  tabActive: {
    ...ui.tab,
    ...ui.tabActive,
    color: component.nav.activeText,
    borderBottom: `2px solid ${component.nav.activeIndicator}`,
  } as React.CSSProperties,
  tabInactive: {
    ...ui.tab,
    color: tokens.color.text.secondary,
  } as React.CSSProperties,
} as const;
