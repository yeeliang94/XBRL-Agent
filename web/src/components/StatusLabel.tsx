import type { CSSProperties, ReactNode } from "react";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS, type StatusSymbol } from "../lib/runStatus";

// The one monochrome status component (design-system Status). Routine status
// is a neutral symbol plus explicit text — never a coloured dot, pill,
// border, or fill. The symbol is aria-hidden; the label is the accessible
// name. Semantic states map to the canonical symbol families in
// lib/runStatus.ts (○ ✓ ! × – ◇).

export type StatusState =
  | "inProgress"
  | "success"
  | "attention"
  | "failure"
  | "inactive"
  | "derived";

interface StatusLabelProps {
  /** Semantic state — picks the canonical neutral symbol. */
  state: StatusState;
  /** Explicit human label; carries the precise meaning and is the
   *  accessible name. */
  label: ReactNode;
  /** Optional supporting description rendered after the label in the
   *  secondary text role. */
  description?: ReactNode;
  /** Override the symbol when a status map (runStatusDisplay) already
   *  resolved one. */
  symbol?: StatusSymbol;
  style?: CSSProperties;
}

export function StatusLabel({ state, label, description, symbol, style }: StatusLabelProps) {
  return (
    <span style={{ ...ui.status, ...style }}>
      <span aria-hidden="true" style={ui.statusSymbol}>
        {symbol ?? STATUS_SYMBOLS[state]}
      </span>
      <span>{label}</span>
      {description && <span style={{ ...ui.metadata, whiteSpace: "normal" }}>{description}</span>}
    </span>
  );
}
