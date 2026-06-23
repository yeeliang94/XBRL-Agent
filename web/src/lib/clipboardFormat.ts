// Notes-table clipboard formatting options.
//
// The clipboard decorator (`clipboard.ts`) injects inline styles into a notes
// cell's HTML at copy time so a paste into M-Tool / Word / Outlook keeps its
// table borders, font, padding and spacing (the DB / sanitiser stay style-free
// — gotcha #16). Those styles used to be hard-coded constants; this module
// makes them user-configurable.
//
// These are GLOBAL defaults, persisted per-browser in localStorage and edited
// in the General settings tab. They describe the receiving paste target
// (M-Tool, Word, Outlook), not the note being edited. Per-note formatting,
// including a totals double underline, belongs in the editor and is persisted
// with the document instead of being hidden in a one-off copy popover.

export type BorderStyle = "none" | "single" | "double";

export interface ClipboardFormatOptions {
  /** Grid-line style applied to every table cell. */
  borderStyle: BorderStyle;
  /** Font size in points (M-Tool interprets a bare size as points). */
  fontSizePt: number;
  /** Cell padding as [vertical, horizontal] in px. */
  cellPaddingPx: [number, number];
  /** Bottom margin (px) between consecutive prose paragraphs. */
  paragraphSpacingPx: number;
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
const BORDER_STYLES: readonly BorderStyle[] = ["none", "single", "double"];

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

/** Read the saved global default from localStorage, falling back to the
 *  built-in defaults. Tolerates a missing key, malformed JSON, or partial
 *  objects (any missing field falls back to its default) so a corrupt value
 *  can never break the settings form or a copy. */
export function loadGlobalFormat(): ClipboardFormatOptions {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_FORMAT_OPTIONS };
    const parsed = JSON.parse(raw) as Partial<GlobalFormat>;
    return {
      // Enum membership check — an unknown string falls back to the default
      // rather than slipping through to render as an accidental "single".
      borderStyle: BORDER_STYLES.includes(parsed.borderStyle as BorderStyle)
        ? (parsed.borderStyle as BorderStyle)
        : DEFAULT_FORMAT_OPTIONS.borderStyle,
      fontSizePt: clampNum(
        parsed.fontSizePt,
        DEFAULT_FORMAT_OPTIONS.fontSizePt,
        FONT_PT,
      ),
      cellPaddingPx: validatePadding(parsed.cellPaddingPx),
      paragraphSpacingPx: clampNum(
        parsed.paragraphSpacingPx,
        DEFAULT_FORMAT_OPTIONS.paragraphSpacingPx,
        PARA_PX,
      ),
    };
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
  };
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(toStore));
  } catch {
    /* storage unavailable — preference simply doesn't persist this session */
  }
}
