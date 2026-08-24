import { ArrowClockwise, Gear, X } from "./iconGlyphs";

// Shared icon components. One icon family (the vendored bold-weight paths in
// iconGlyphs.tsx) across the app so every glyph shares a stroke weight and
// baseline on every OS — the earlier Unicode-entity icons (✕ ↻) were drawn by
// whichever fallback font Windows had to hand. Each icon renders
// `aria-hidden="true"` so screen readers use the parent button's aria-label
// instead. Status symbols are a separate primitive (StatusIcon.tsx).

export function CloseIcon({ size = 1 }: { size?: number }) {
  // `size` is a unitless scale of the current font-size (legacy API) so
  // callers keep the visual weight they had with the text glyph.
  return (
    <span aria-hidden="true" style={{ display: "inline-flex", lineHeight: 1 }}>
      <X size={`${size}em`} />
    </span>
  );
}

export function RerunIcon({ size = 1 }: { size?: number }) {
  return (
    <span aria-hidden="true" style={{ display: "inline-flex", lineHeight: 1 }}>
      <ArrowClockwise size={`${size}em`} />
    </span>
  );
}

export function SettingsIcon({ size = 20 }: { size?: number }) {
  return <Gear aria-hidden="true" size={size} />;
}
