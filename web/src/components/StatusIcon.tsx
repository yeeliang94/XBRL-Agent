import type { CSSProperties } from "react";
import type { Glyph } from "./iconGlyphs";
import {
  Cancel,
  CheckCircle,
  DoNotDisturbOn,
  ErrorCircle,
  Functions,
  ProgressActivity,
} from "./iconGlyphs";
import { ui } from "../lib/uiStyles";
import { tokens } from "../lib/theme";
import { STATUS_SYMBOLS, type StatusSymbol } from "../lib/runStatus";

// ---------------------------------------------------------------------------
// StatusIcon — the ONE renderer for the status symbol families
// (design-system Status). The families are still keyed by the canonical
// text symbol in lib/runStatus.ts (○ ✓ ! × – ◇) so status maps, tests and
// plain-text contexts keep a stable vocabulary; on screen each family draws a
// Material Symbols Rounded icon in its family colour.
//
// Rules:
//   - aria-hidden: the explicit text label beside it is the accessible name.
//   - colour comes from the family (orange = working or needs attention,
//     black = complete, grey = inactive/derived); callers may override it
//     only on the exceptional surfaces that already did.
//   - one family per user-facing concept, never one per backend enum.
// ---------------------------------------------------------------------------

interface StatusIconDef {
  /** SVG icon component from iconGlyphs. */
  Icon: Glyph;
  /** Stable machine name — surfaced as data-status-icon for tests/styling. */
  name: string;
  /** Family colour from the icon colour roles in lib/theme.ts. */
  color: string;
}

const STATUS_ICONS: Record<StatusSymbol, StatusIconDef> = {
  [STATUS_SYMBOLS.inProgress]: { Icon: ProgressActivity, name: "in-progress", color: tokens.color.icon.active },
  [STATUS_SYMBOLS.success]: { Icon: CheckCircle, name: "success", color: tokens.color.icon.strong },
  [STATUS_SYMBOLS.attention]: { Icon: ErrorCircle, name: "attention", color: tokens.color.icon.attention },
  [STATUS_SYMBOLS.failure]: { Icon: Cancel, name: "failure", color: tokens.color.icon.attention },
  [STATUS_SYMBOLS.inactive]: { Icon: DoNotDisturbOn, name: "inactive", color: tokens.color.icon.rest },
  [STATUS_SYMBOLS.derived]: { Icon: Functions, name: "derived", color: tokens.color.icon.rest },
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

/** Default icon box in px — sits beside 13–14px status text. */
export const STATUS_ICON_SIZE = 16;

interface StatusIconProps {
  /** Canonical symbol from STATUS_SYMBOLS / a status map's `symbol`. */
  symbol: StatusSymbol;
  /** Icon box in px (default 16). */
  size?: number;
  /** Spread over the family colour — e.g. a colour on an exceptional surface. */
  style?: CSSProperties;
  "data-testid"?: string;
}

export function StatusIcon({ symbol, size = STATUS_ICON_SIZE, style, "data-testid": testId }: StatusIconProps) {
  const { Icon, name, color } = STATUS_ICONS[symbol] ?? STATUS_ICONS[STATUS_SYMBOLS.inactive];
  return (
    <span
      aria-hidden="true"
      data-status-icon={name}
      data-testid={testId}
      className={name === "in-progress" ? "status-icon-working" : undefined}
      style={{ ...ui.statusSymbol, width: size, color, ...style }}
    >
      <Icon size={size} />
    </span>
  );
}
