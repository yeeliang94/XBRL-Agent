// Notes-table clipboard formatting options.
//
// The clipboard decorator (`clipboard.ts`) injects inline styles into a notes
// cell's HTML at copy time so a paste into M-Tool / Word / Outlook keeps its
// table borders, font, padding and spacing (the DB / sanitiser stay style-free
// — gotcha #16). Those styles used to be hard-coded constants; this module
// makes them user-configurable.
//
// This shape is now the **notes table style theme** (docs/PLAN-notes-table-theme.md):
// one preset that drives BOTH the in-editor table preview (via CSS variables on
// the notes root) AND the clipboard paste, so what you see equals what you
// paste. It is sourced server-side as a firm-wide default (Settings) with an
// optional per-run override; this module owns the shape, validation, and the
// resolution order. (The legacy localStorage load/save below is retained for
// back-compat but the firm default now lives on the server.)

export type BorderStyle = "none" | "single" | "double";
export type ListMarker = "disc" | "dash" | "decimal";

export interface ClipboardFormatOptions {
  /** Grid-line style applied to every table cell. */
  borderStyle: BorderStyle;
  /** Font size in points (M-Tool interprets a bare size as points). */
  fontSizePt: number;
  /** Cell padding as [vertical, horizontal] in px. */
  cellPaddingPx: [number, number];
  /** Bottom margin (px) between consecutive prose paragraphs. */
  paragraphSpacingPx: number;
  /** Grid-line colour (hex or `transparent`). Theme addition: when ABSENT each
   *  surface keeps its own historic default (editor `#C9C9C9`, clipboard
   *  `#999`); when SET it applies to both so they match. Optional precisely so
   *  an un-customised default stays byte-for-byte what shipped before. */
  borderColor?: string;
  /** Header-row fill colour (hex or `transparent`). Absent → editor `#F4F4F4`,
   *  clipboard `#f3f4f6` (historic defaults, byte-compatible). */
  headerFill?: string;
  /** Whether header cells render bold. Absent → true (the historic behaviour). */
  headerBold?: boolean;
  /** Heading (`<h3>`) font size in points. Absent → each surface keeps its
   *  historic default (editor 15px, clipboard/mTool the body size). */
  headingSizePt?: number;
  /** Heading font weight (400–800). Absent → the historic 600. */
  headingWeight?: number;
  /** `<ul>` bullet glyph. Absent → the target's default disc. */
  listMarker?: ListMarker;
  /** Accountant totals convention: 3px double rule under the amount cells of
   *  "total" rows. Absent/false → no decoration (historic output). */
  totalsDoubleUnderline?: boolean;
  /** Accountant "ruled" look: one horizontal rule under the header row, with no
   *  cell grid (`borderStyle: "none"`). Printed statements are ruled, not
   *  boxed — and it matches what a Word source produces. Absent/false → no rule
   *  (historic output). The shipped firm default turns this on; see
   *  `server.HOUSE_NOTES_TABLE_STYLE`. */
  headerRule?: boolean;
}

// Defaults reproduce the previously hard-coded clipboard styling EXACTLY
// (single 1px #999 grid, Arial 10pt, 4×8px padding, 8px paragraph gap) so a
// copy with default options is byte-for-byte what shipped
// before this feature — the existing clipboard pinning tests depend on it.
export const DEFAULT_FORMAT_OPTIONS: ClipboardFormatOptions = {
  borderStyle: "single",
  fontSizePt: 10,
  cellPaddingPx: [4, 8],
  paragraphSpacingPx: 8,
};

// localStorage key for the per-browser global default.
const STORAGE_KEY = "xbrl.notesClipboardFormat";

type GlobalFormat = ClipboardFormatOptions;

// Clamp ranges mirror the input controls (ClipboardFormatControls) so a value
// that survived the form is never re-clamped, but a tampered / out-of-range
// stored value is brought back into bounds instead of producing broken CSS.
const FONT_PT = { min: 6, max: 24 } as const;
const PADDING_PX = { min: 0, max: 32 } as const;
const PARA_PX = { min: 0, max: 48 } as const;
const HEADING_WEIGHT = { min: 400, max: 800 } as const;
const BORDER_STYLES: readonly BorderStyle[] = ["none", "single", "double"];
const LIST_MARKERS: readonly ListMarker[] = ["disc", "dash", "decimal"];

// Colour shape check mirrors the backend sanitiser's `_HEX_COLOR_RE` + the
// `transparent` keyword (notes/html_sanitize.py). A stored / server-sent value
// that isn't a safe colour is DROPPED (returns undefined) so it falls back to
// the surface's historic default rather than rendering broken CSS.
const HEX_COLOR_RE = /^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
function validColor(v: unknown): string | undefined {
  if (typeof v !== "string") return undefined;
  const s = v.trim().toLowerCase();
  return s === "transparent" || HEX_COLOR_RE.test(s) ? s : undefined;
}

/** Coerce a stored value to a finite number clamped to [min, max], falling
 *  back to `fallback` when it isn't a finite number at all. */
function clampNum(
  v: unknown,
  fallback: number,
  range: { min: number; max: number },
): number {
  if (typeof v !== "number" || !Number.isFinite(v)) return fallback;
  return Math.min(range.max, Math.max(range.min, v));
}

/** Validate a stored cell-padding value: must be a 2-element array of finite
 *  numbers (a malformed `[0]` would otherwise yield `padding: 0px undefinedpx`).
 *  Each element is clamped; any deviation falls back to the default tuple. */
function validatePadding(v: unknown): [number, number] {
  if (!Array.isArray(v) || v.length !== 2) {
    return DEFAULT_FORMAT_OPTIONS.cellPaddingPx;
  }
  const [a, b] = v;
  if (
    typeof a !== "number" ||
    typeof b !== "number" ||
    !Number.isFinite(a) ||
    !Number.isFinite(b)
  ) {
    return DEFAULT_FORMAT_OPTIONS.cellPaddingPx;
  }
  return [
    clampNum(a, DEFAULT_FORMAT_OPTIONS.cellPaddingPx[0], PADDING_PX),
    clampNum(b, DEFAULT_FORMAT_OPTIONS.cellPaddingPx[1], PADDING_PX),
  ];
}

/** Validate + clamp an untrusted theme object (from localStorage OR the server
 *  firm-default / per-run override) into a safe `ClipboardFormatOptions`. Any
 *  missing or malformed field falls back to its default; the optional colour
 *  fields are dropped (left absent) when not a safe colour, so the surface's
 *  historic default shows through. Single source of truth so the localStorage
 *  and server paths validate identically. */
export function parseThemeOptions(
  parsed: Partial<ClipboardFormatOptions> | null | undefined,
): ClipboardFormatOptions {
  const p = parsed ?? {};
  const out: ClipboardFormatOptions = {
    // Enum membership check — an unknown string falls back to the default
    // rather than slipping through to render as an accidental "single".
    borderStyle: BORDER_STYLES.includes(p.borderStyle as BorderStyle)
      ? (p.borderStyle as BorderStyle)
      : DEFAULT_FORMAT_OPTIONS.borderStyle,
    fontSizePt: clampNum(p.fontSizePt, DEFAULT_FORMAT_OPTIONS.fontSizePt, FONT_PT),
    cellPaddingPx: validatePadding(p.cellPaddingPx),
    paragraphSpacingPx: clampNum(
      p.paragraphSpacingPx,
      DEFAULT_FORMAT_OPTIONS.paragraphSpacingPx,
      PARA_PX,
    ),
  };
  // Optional colour / bold fields: only attach when valid, so an absent or
  // bad value leaves the field undefined (→ surface default), never a broken
  // CSS string.
  const bc = validColor(p.borderColor);
  if (bc) out.borderColor = bc;
  const hf = validColor(p.headerFill);
  if (hf) out.headerFill = hf;
  if (typeof p.headerBold === "boolean") out.headerBold = p.headerBold;
  // Prose theme fields (house style item 1): same only-attach-when-valid
  // discipline, so an un-customised default keeps its historic shape.
  if (typeof p.headingSizePt === "number" && Number.isFinite(p.headingSizePt)) {
    out.headingSizePt = clampNum(p.headingSizePt, 10, FONT_PT);
  }
  if (typeof p.headingWeight === "number" && Number.isFinite(p.headingWeight)) {
    out.headingWeight = clampNum(p.headingWeight, 600, HEADING_WEIGHT);
  }
  if (LIST_MARKERS.includes(p.listMarker as ListMarker)) {
    out.listMarker = p.listMarker as ListMarker;
  }
  if (typeof p.totalsDoubleUnderline === "boolean") {
    out.totalsDoubleUnderline = p.totalsDoubleUnderline;
  }
  if (typeof p.headerRule === "boolean") {
    out.headerRule = p.headerRule;
  }
  return out;
}

/** Resolve the EFFECTIVE theme for a surface: per-run override wins over the
 *  firm default wins over the built-in default. Each layer contributes only the
 *  fields it actually sets (a run that overrides just the border colour still
 *  inherits the firm font size), then the whole thing is validated. */
export function resolveTheme(
  runOverride: Partial<ClipboardFormatOptions> | null | undefined,
  firmDefault: Partial<ClipboardFormatOptions> | null | undefined,
): ClipboardFormatOptions {
  return parseThemeOptions({
    ...DEFAULT_FORMAT_OPTIONS,
    ...(firmDefault ?? {}),
    ...(runOverride ?? {}),
  });
}

/** Map a resolved theme to the `--nt-*` CSS custom properties the notes editor
 *  reads (`NotesReviewTab.css`). Set on the `.notes-review-tab` root so the
 *  editor preview matches the clipboard paste. Crucially, the BUILT-IN default
 *  theme maps to the editor's HISTORIC values (1px solid #c9c9c9 grid, #f4f4f4
 *  header, 13px) — so an un-customised install looks unchanged. A per-cell
 *  inline style still wins over these (CSS specificity: inline > rule). */
export function themeToCssVars(theme: ClipboardFormatOptions): Record<string, string> {
  // Grid line: the editor's historic default colour is #c9c9c9 (softer than the
  // clipboard's #999) — used only when the theme leaves the colour unset.
  const gridColor = theme.borderColor ?? "#c9c9c9";
  const gridBorder =
    theme.borderStyle === "none"
      ? "none"
      : theme.borderStyle === "double"
        ? `3px double ${gridColor}`
        : `1px solid ${gridColor}`;
  // pt → px for the on-screen preview. The default 10pt rounds to 13px, exactly
  // the editor's historic font size, so the default is unchanged.
  const fontPx = Math.round(theme.fontSizePt * 1.3333);
  // Heading size: pt → px like the body font. Absent keeps the editor's
  // historic 15px (the clipboard keeps the body size — same per-surface
  // historic-default split as borderColor).
  const headingPx =
    theme.headingSizePt !== undefined
      ? `${Math.round(theme.headingSizePt * 1.3333)}px`
      : "15px";
  // Bullet glyph for `<ul>`: the dash variant is a CSS string marker.
  const listMarker =
    theme.listMarker === "dash"
      ? '"– "'
      : (theme.listMarker ?? "disc");
  const vars: Record<string, string> = {
    "--nt-grid-border": gridBorder,
    "--nt-cell-padding": `${theme.cellPaddingPx[0]}px ${theme.cellPaddingPx[1]}px`,
    "--nt-cell-font-size": `${fontPx}px`,
    "--nt-header-fill": theme.headerFill ?? "#f4f4f4",
    "--nt-header-weight": theme.headerBold === false ? "400" : "600",
    // Paragraph gap — drives the editor's `.tiptap p` margin so the on-screen
    // spacing matches the clipboard paste (peer-review HIGH #1). Default 8px is
    // the editor's historic value, so an un-themed install is unchanged.
    "--nt-para-spacing": `${theme.paragraphSpacingPx}px`,
    // Prose theme fields (house style item 1) — defaults mirror the editor's
    // historic h3 (15px / 600) and browser-default disc bullets.
    "--nt-heading-size": headingPx,
    "--nt-heading-weight": `${theme.headingWeight ?? 600}`,
    "--nt-list-marker": listMarker,
  };
  // Totals double rule: the variable is only SET when the convention is on —
  // the stylesheet's `.is-totals-num` rule falls back to the normal grid
  // border when unset, so an un-themed editor looks exactly as before.
  if (theme.totalsDoubleUnderline === true) {
    vars["--nt-totals-border"] = "3px double #000000";
  }
  // Header rule (accountant "ruled" look). Same discipline as the totals rule:
  // the variable is only SET when the convention is on, so the stylesheet's
  // fallback keeps an un-themed editor byte-identical to before.
  if (theme.headerRule === true) {
    vars["--nt-header-rule"] = `1px solid ${theme.borderColor ?? "#999"}`;
  }
  return vars;
}

/** Read the saved global default from localStorage, falling back to the
 *  built-in defaults. Tolerates a missing key, malformed JSON, or partial
 *  objects so a corrupt value can never break the settings form or a copy.
 *  (Legacy per-browser path; the firm default now lives on the server.) */
export function loadGlobalFormat(): ClipboardFormatOptions {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_FORMAT_OPTIONS };
    return parseThemeOptions(JSON.parse(raw) as Partial<ClipboardFormatOptions>);
  } catch {
    // Storage unavailable (private mode, SSR) or unparseable — defaults.
    return { ...DEFAULT_FORMAT_OPTIONS };
  }
}

/** Persist the per-browser global defaults. Swallows storage errors (quota /
 *  private mode) so saving a preference never throws into the UI. */
export function saveGlobalFormat(opts: ClipboardFormatOptions): void {
  const toStore: GlobalFormat = {
    borderStyle: opts.borderStyle,
    fontSizePt: opts.fontSizePt,
    cellPaddingPx: opts.cellPaddingPx,
    paragraphSpacingPx: opts.paragraphSpacingPx,
    // Only persist the optional theme fields when set, so the stored shape of
    // an un-customised default stays exactly as before.
    ...(opts.borderColor ? { borderColor: opts.borderColor } : {}),
    ...(opts.headerFill ? { headerFill: opts.headerFill } : {}),
    ...(typeof opts.headerBold === "boolean" ? { headerBold: opts.headerBold } : {}),
    ...(typeof opts.headingSizePt === "number" ? { headingSizePt: opts.headingSizePt } : {}),
    ...(typeof opts.headingWeight === "number" ? { headingWeight: opts.headingWeight } : {}),
    ...(opts.listMarker ? { listMarker: opts.listMarker } : {}),
    ...(typeof opts.totalsDoubleUnderline === "boolean"
      ? { totalsDoubleUnderline: opts.totalsDoubleUnderline }
      : {}),
  };
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(toStore));
  } catch {
    /* storage unavailable — preference simply doesn't persist this session */
  }
}
