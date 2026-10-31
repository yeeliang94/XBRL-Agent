import type { CSSProperties, ReactNode } from "react";
import { ds } from "../lib/theme";
import { ui } from "../lib/uiStyles";

// Shared empty state (design-system Content & states): a clear title, an
// explanation, and the next action when one exists. Layout stays stable and
// quiet — no illustration, no card.

interface EmptyStateProps {
  title: ReactNode;
  /** Plain-language explanation of why the surface is empty. */
  explanation?: ReactNode;
  /** Optional next action (a button or link element). */
  action?: ReactNode;
  style?: CSSProperties;
}

export function EmptyState({ title, explanation, action, style }: EmptyStateProps) {
  return (
    <div className="app-empty-state" style={{ ...ui.emptyState, ...style }}>
      <div
        style={{
          fontFamily: ds.fontHeading,
          fontSize: 14,
          fontWeight: ds.weight.semibold,
          color: ds.grey900,
        }}
      >
        {title}
      </div>
      {explanation && (
        <p style={{ ...ui.supportingText, margin: `${ds.space.sm}px 0 0` }}>{explanation}</p>
      )}
      {action && <div style={{ marginTop: ds.space.lg }}>{action}</div>}
    </div>
  );
}
