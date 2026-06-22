// Constrained house palette for the notes editor (notes editor v2 — decision:
// "text colour = constrained palette, not a full picker"). Keeping the set
// small keeps the firm's deliverables on-brand and the toolbar uncluttered.
//
// These are the ONLY colours the toolbar offers. The backend sanitiser does
// NOT re-enforce this exact list — it validates that a colour is a *safe
// value* (hex/rgb, no url()/expression()); constraining to the palette is a
// UX/house-style concern best owned by the toolbar, and avoids a brittle
// cross-language list to keep in sync (the very coupling v2 set out to remove).
// `value: null` is the reset (remove colour / no highlight).

export interface PaletteSwatch {
  label: string;
  value: string | null;
}

// Text colour (TipTap Color → <span style="color: …">). Black is the default
// body colour, the rest are muted PwC-adjacent accents for emphasis.
export const TEXT_COLORS: ReadonlyArray<PaletteSwatch> = [
  { label: "Default", value: null },
  { label: "Black", value: "#1a1a1a" },
  { label: "Orange", value: "#fd5108" },
  { label: "Blue", value: "#185fa5" },
  { label: "Green", value: "#0f6e56" },
  { label: "Red", value: "#a32d2d" },
];

// Highlight fill (TipTap Highlight multicolor → <mark style="background-color: …">).
// Soft tints so dark text stays readable on top.
export const HIGHLIGHT_COLORS: ReadonlyArray<PaletteSwatch> = [
  { label: "None", value: null },
  { label: "Yellow", value: "#fff3b0" },
  { label: "Grey", value: "#f1efe8" },
  { label: "Green", value: "#d9f0e3" },
  { label: "Blue", value: "#e6f1fb" },
];
