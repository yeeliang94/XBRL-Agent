import type { CSSProperties } from "react";
import type { Glyph } from "./iconGlyphs";
import { Check, Circle, Diamond, ExclamationMark, Minus, X } from "./iconGlyphs";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS, type StatusSymbol } from "../lib/runStatus";

// ---------------------------------------------------------------------------
// StatusIcon — the ONE renderer for the monochrome status symbol families
// (design-system Status). The families are still keyed by the canonical
// text symbol in lib/runStatus.ts (○ ✓ ! × – ◇) so status maps, tests and
// plain-text contexts keep a stable vocabulary; on screen each family draws an
// SVG icon at one stroke weight instead of a font glyph. Font glyphs
// came from whichever fallback face the OS had (Segoe UI Symbol on Windows),
// so ○ and ◇ rendered at different sizes and weights from ✓ and ×.
//
// Rules carried over from the glyph version:
//   - aria-hidden: the explicit text label beside it is the accessible name.
//   - neutral colour (ui.statusSymbol → text.secondary); callers may pass a
//     colour override only on the exceptional surfaces that already did.
//   - one family per user-facing concept, never one per backend enum.
// ---------------------------------------------------------------------------

interface StatusIconDef {
  /** SVG icon component from iconGlyphs. */
  Icon: Glyph;
  /** Stable machine name — surfaced as data-status-icon for tests/styling. */
  name: string;
}

const STATUS_ICONS: Record<StatusSymbol, StatusIconDef> = {
  [STATUS_SYMBOLS.inProgress]: { Icon: Circle, name: "in-progress" },
  [STATUS_SYMBOLS.success]: { Icon: Check, name: "success" },
  [STATUS_SYMBOLS.attention]: { Icon: ExclamationMark, name: "attention" },
  [STATUS_SYMBOLS.failure]: { Icon: X, name: "failure" },
  [STATUS_SYMBOLS.inactive]: { Icon: Minus, name: "inactive" },
  [STATUS_SYMBOLS.derived]: { Icon: Diamond, name: "derived" },
};

/** Machine names per family, exported for the design-system parity test. */
export const STATUS_ICON_NAMES: Record<keyof typeof STATUS_SYMBOLS, string> = {
  inProgress: STATUS_ICONS[STATUS_SYMBOLS.inProgress].name,
  success: STATUS_ICONS[STATUS_SYMBOLS.success].name,
  attention: STATUS_ICONS[STATUS_SYMBOLS.attention].name,
  failure: STATUS_ICONS[STATUS_SYMBOLS.failure].name,
  inactive: STATUS_ICONS[STATUS_SYMBOLS.inactive].name,
  derived: STATUS_ICONS[STATUS_SYMBOLS.derived].name,
};

/** Default icon box in px — matches the 13px status text it sits beside. */
export const STATUS_ICON_SIZE = 13;

interface StatusIconProps {
  /** Canonical symbol from STATUS_SYMBOLS / a status map's `symbol`. */
  symbol: StatusSymbol;
  /** Icon box in px (default 13). */
  size?: number;
  /** Spread over ui.statusSymbol — e.g. a colour on an exceptional surface. */
  style?: CSSProperties;
  "data-testid"?: string;
}

export function StatusIcon({ symbol, size = STATUS_ICON_SIZE, style, "data-testid": testId }: StatusIconProps) {
  const { Icon, name } = STATUS_ICONS[symbol] ?? STATUS_ICONS[STATUS_SYMBOLS.inactive];
  return (
    <span
      aria-hidden="true"
      data-status-icon={name}
      data-testid={testId}
      style={{ ...ui.statusSymbol, ...style }}
    >
      <Icon size={size} />
    </span>
  );
}
