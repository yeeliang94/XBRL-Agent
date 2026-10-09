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
    fontSize: 12,
    fontWeight: pwc.weight.medium,
    color: tokens.color.text.secondary,
    marginBottom: pwc.space.sm,
  },
  title: {
    ...ui.pageTitle,
  },
  titleCompact: {
    ...ui.pageTitleCompact,
    letterSpacing: 0,
  },
  description: {
    ...ui.bodyText,
    color: tokens.color.text.secondary,
    maxWidth: "70ch",
    marginTop: pwc.space.sm,
    marginBottom: 0,
  },
  actions: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    flexShrink: 0,
  },
};
