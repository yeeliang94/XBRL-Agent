import type { CSSProperties, ReactNode } from "react";
import { pwc } from "../lib/theme";

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
    <header style={compact ? styles.wrapCompact : styles.wrap}>
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
  alignItems: "flex-end",
  gap: pwc.space.lg,
  flexWrap: "wrap",
  borderBottom: `1px solid ${pwc.grey200}`,
};

const styles: Record<string, CSSProperties> = {
  wrap: {
    ...wrapBase,
    paddingBottom: pwc.space.lg,
    marginBottom: 0,
  },
  wrapCompact: {
    ...wrapBase,
    paddingBottom: pwc.space.md,
    marginBottom: 0,
  },
  textCol: {
    minWidth: 0,
  },
  eyebrow: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: pwc.weight.medium,
    textTransform: "uppercase",
    letterSpacing: 0,
    color: pwc.orange500,
    marginBottom: pwc.space.sm,
  },
  title: {
    fontFamily: pwc.fontHeading,
    // Large + light: hierarchy from scale, not weight (design principle).
    // Slightly under the reference's 38px because this app keeps a global
    // brand bar above the page, so the page title shouldn't compete with it.
    fontSize: 32,
    fontWeight: pwc.weight.regular,
    letterSpacing: 0,
    lineHeight: 1.1,
    color: pwc.grey900,
    margin: 0,
  },
  titleCompact: {
    fontFamily: pwc.fontHeading,
    fontSize: 22,
    fontWeight: pwc.weight.regular,
    letterSpacing: 0,
    lineHeight: 1.2,
    color: pwc.grey900,
    margin: 0,
  },
  description: {
    fontFamily: pwc.fontBody,
    fontSize: 15,
    fontWeight: pwc.weight.regular,
    lineHeight: 1.55,
    color: pwc.grey500,
    maxWidth: "60ch",
    marginTop: pwc.space.md,
    marginBottom: 0,
  },
  actions: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    flexShrink: 0,
  },
};
