import type { MouseEvent, ReactNode } from "react";
import { pwc, tokens } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import type { AppView } from "../lib/appReducer";
import { TERMS } from "../lib/vocabulary";

export type CurrentFilingTab = "overview" | "values" | "notes";

export interface TopNavProps {
  view: AppView;
  onViewChange: (view: AppView) => void;
  showConcepts?: boolean;
  isAdmin?: boolean;
  extractMode?: "queue" | "new";
  currentRunId?: number | null;
  currentFilingTab?: CurrentFilingTab | null;
  onNewExtraction?: () => void;
  onOpenCurrentFiling?: (tab: CurrentFilingTab) => void;
}

const TOOLS: {
  id: AppView;
  label: string;
  href: string;
  glyph: string;
  adminOnly?: boolean;
  canonicalOnly?: boolean;
}[] = [
  { id: "concepts", label: "Field labels", href: "/field-labels", glyph: "Aa", adminOnly: true, canonicalOnly: true },
  { id: "benchmarks", label: "Benchmarks", href: "/benchmarks", glyph: "◇", canonicalOnly: true },
  { id: "suites", label: TERMS.evaluationSuites, href: "/evals", glyph: "✓", canonicalOnly: true },
];

export function TopNav({
  view,
  onViewChange,
  showConcepts = true,
  isAdmin = false,
  extractMode = "queue",
  currentRunId = null,
  currentFilingTab = "overview",
  onNewExtraction,
  onOpenCurrentFiling,
}: TopNavProps) {
  const tools = TOOLS.filter((item) => {
    if (item.adminOnly && !isAdmin) return false;
    if (item.canonicalOnly && !showConcepts) return false;
    return true;
  });

  const intercept = (event: MouseEvent<HTMLAnchorElement>, action: () => void) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    action();
  };

  const link = ({ key, href, label, glyph, active, action }: {
    key: string;
    href: string;
    label: string;
    glyph: ReactNode;
    active: boolean;
    action: () => void;
  }) => (
    <a
      key={key}
      href={href}
      aria-current={active ? "page" : undefined}
      onClick={(event) => intercept(event, action)}
      className="app-main-nav-tab"
      style={active ? styles.tabActive : styles.tabInactive}
    >
      <span className="app-main-nav-glyph" style={active ? styles.glyphActive : styles.glyph} aria-hidden="true">
        {glyph}
      </span>
      <span className="app-main-nav-label">{label}</span>
    </a>
  );

  return (
    <nav className="app-main-nav" style={styles.nav} aria-label="Main navigation">
      {link({ key: "work-queue", href: "/", label: "Work queue", glyph: "⌂", active: view === "extract" && extractMode === "queue" && currentRunId == null, action: () => onViewChange("extract") })}
      {link({ key: "new-extraction", href: "/#new-extraction", label: "New extraction", glyph: "＋", active: view === "extract" && extractMode === "new" && currentRunId == null, action: () => onNewExtraction?.() })}
      {link({ key: "runs", href: "/history", label: TERMS.runs, glyph: "▤", active: view === "history" && currentRunId == null, action: () => onViewChange("history") })}

      {currentRunId != null && (
        <div className="app-nav-current" style={{ display: "contents" }}>
          <span className="app-rail-section-label" style={styles.groupLabel}>Current filing</span>
          {link({ key: "filing-overview", href: `/history/${currentRunId}?tab=overview`, label: "Overview", glyph: "◉", active: currentFilingTab === "overview" && (view === "extract" || view === "history" || view === "concepts"), action: () => onOpenCurrentFiling?.("overview") })}
          {link({ key: "figures-review", href: `/concepts/${currentRunId}`, label: "Figures review", glyph: "⌗", active: currentFilingTab === "values" && (view === "history" || view === "concepts"), action: () => onOpenCurrentFiling?.("values") })}
          {link({ key: "notes-review", href: `/history/${currentRunId}?tab=notes`, label: "Notes review", glyph: "¶", active: currentFilingTab === "notes" && (view === "history" || view === "concepts"), action: () => onOpenCurrentFiling?.("notes") })}
        </div>
      )}

      {tools.length > 0 && (
        <div className="app-nav-tools" style={{ display: "contents" }}>
          <span className="app-rail-section-label" style={styles.groupLabel}>Tools</span>
          {tools.map((item) => link({ key: item.id, href: item.href, label: item.label, glyph: item.glyph, active: item.id === view && currentRunId == null, action: () => onViewChange(item.id) }))}
        </div>
      )}
    </nav>
  );
}

const styles = {
  nav: {
    display: "flex",
    flexDirection: "column",
    alignItems: "stretch",
    gap: 2,
  } as React.CSSProperties,
  groupLabel: {
    padding: "16px 10px 7px",
    color: tokens.color.text.muted,
    fontFamily: pwc.fontBody,
    fontSize: 10,
    fontWeight: pwc.weight.bold,
    letterSpacing: "0.09em",
    textTransform: "uppercase",
  } as React.CSSProperties,
  tabActive: {
    ...ui.buttonQuiet,
    minHeight: 38,
    justifyContent: "flex-start",
    padding: "0 10px",
    borderRadius: 7,
    color: pwc.black,
    background: pwc.white,
    boxShadow: "0 1px 2px rgba(24, 24, 22, 0.04)",
    fontWeight: pwc.weight.medium,
  } as React.CSSProperties,
  tabInactive: {
    ...ui.buttonQuiet,
    minHeight: 38,
    justifyContent: "flex-start",
    padding: "0 10px",
    borderRadius: 7,
    color: tokens.color.text.secondary,
  } as React.CSSProperties,
  glyph: {
    display: "inline-block",
    width: 18,
    color: tokens.color.text.muted,
    textAlign: "center",
    fontSize: 12,
    fontWeight: 600,
  } as React.CSSProperties,
  glyphActive: {
    display: "inline-block",
    width: 18,
    color: pwc.orange500,
    textAlign: "center",
    fontSize: 12,
    fontWeight: 600,
  } as React.CSSProperties,
} as const;
