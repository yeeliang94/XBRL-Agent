import type { CSSProperties, ReactNode } from "react";
import { pwc, tokens } from "../lib/theme";
import { ui } from "../lib/uiStyles";

// Reusable page title chrome. Keep it quiet: title, optional actions, and a
// rule. Extra explanatory copy belongs in task-specific empty/error states.

interface PageHeaderProps {
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
}

export function PageHeader({ eyebrow, title, description, actions, compact = false }: PageHeaderProps) {
  return (
    <header className="page-header" style={compact ? styles.wrapCompact : styles.wrap}>
      <div style={styles.textCol}>
        {eyebrow && <div style={styles.eyebrow}>{eyebrow}</div>}
        <h1 style={compact ? styles.titleCompact : styles.title}>{title}</h1>
        {description && <p style={styles.description}>{description}</p>}
      </div>
      {actions && <div style={styles.actions}>{actions}</div>}
    </header>
  );
}

const wrapBase: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: pwc.space.lg,
  flexWrap: "wrap",
};

const styles: Record<string, CSSProperties> = {
  wrap: {
    ...wrapBase,
    paddingBottom: 0,
    marginBottom: 0,
  },
  wrapCompact: {
    ...wrapBase,
    paddingBottom: 0,
    marginBottom: 0,
  },
  textCol: {
    minWidth: 0,
  },
  eyebrow: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: pwc.weight.semibold,
    textTransform: "uppercase",
    letterSpacing: 0,
    color: tokens.color.brand.accent,
    marginBottom: pwc.space.sm,
  },
  title: {
    ...ui.pageTitle,
    fontSize: "clamp(24px, 2.2vw, 34px)",
    lineHeight: 1.15,
    fontWeight: pwc.weight.bold,
    letterSpacing: "-0.035em",
  },
  titleCompact: {
    ...ui.pageTitleCompact,
    letterSpacing: 0,
  },
  description: {
    ...ui.bodyText,
    fontSize: 15,
    color: tokens.color.text.secondary,
    maxWidth: 650,
    marginTop: 9,
    marginBottom: 0,
  },
  actions: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    flexShrink: 0,
  },
};
