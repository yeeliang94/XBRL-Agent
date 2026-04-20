// Shared icon components. Consolidates the inline HTML entities (`&#10005;`,
// `&#8635;`, literal `×`) and the settings-gear SVG that used to be scattered
// across App/AgentTabs/SuccessToast/RunDetailModal. Each icon renders as a
// self-contained <span>/<svg> with `aria-hidden="true"` so screen readers use
// the parent button's aria-label instead.

export function CloseIcon({ size = 1 }: { size?: number }) {
  // U+2715 — the same glyph as the old `&#10005;`. `size` is a unitless scale
  // applied to the font-size so callers can keep the current visual weight.
  return (
    <span aria-hidden="true" style={{ fontSize: `${size}em`, lineHeight: 1 }}>
      &#10005;
    </span>
  );
}

export function RerunIcon({ size = 1 }: { size?: number }) {
  // U+21BB — matches the previous `&#8635;` clockwise-open circle.
  return (
    <span aria-hidden="true" style={{ fontSize: `${size}em`, lineHeight: 1 }}>
      &#8635;
    </span>
  );
}

export function SettingsIcon({ size = 20 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  );
}
