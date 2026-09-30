import type { CSSProperties } from "react";
import type { Glyph } from "./iconGlyphs";
import { ChevronRight, Close, Refresh, Settings } from "./iconGlyphs";
import { tokens } from "../lib/theme";

// Shared icon components. One icon family (the vendored Material Symbols
// Rounded paths in iconGlyphs.tsx) across the app so every glyph shares a
// stroke weight and baseline on every OS — typed Unicode symbols (✕ ↻ ▾) were
// drawn by whichever fallback font Windows had to hand. Each icon renders
// `aria-hidden="true"` so screen readers use the parent control's aria-label
// instead. Status symbols are a separate primitive (StatusIcon.tsx).

/** Inline icon box: centres any glyph on the text baseline of its parent. */
export function Icon({
  glyph: G,
  size = 20,
  color,
  style,
}: {
  glyph: Glyph;
  size?: number | string;
  color?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      aria-hidden="true"
      style={{ display: "inline-flex", alignItems: "center", lineHeight: 1, flexShrink: 0, color, ...style }}
    >
      <G size={size} />
    </span>
  );
}

export function CloseIcon({ size = 1 }: { size?: number }) {
  // `size` is a unitless scale of the current font-size (legacy API) so
  // callers keep the visual weight they had with the text glyph.
  return <Icon glyph={Close} size={`${size * 1.25}em`} />;
}

export function RerunIcon({ size = 1 }: { size?: number }) {
  return <Icon glyph={Refresh} size={`${size * 1.25}em`} />;
}

export function SettingsIcon({ size = 20 }: { size?: number }) {
  return <Icon glyph={Settings} size={size} />;
}

/** The one expand/collapse affordance: a chevron that turns down when open
 *  (rotation lives in index.css so reduced motion applies). */
export function DisclosureChevron({ open }: { open: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`pwc-disclosure-chevron${open ? " is-open" : ""}`}
      style={{ display: "inline-flex", flexShrink: 0, color: tokens.color.icon.rest }}
    >
      <ChevronRight size={20} />
    </span>
  );
}
