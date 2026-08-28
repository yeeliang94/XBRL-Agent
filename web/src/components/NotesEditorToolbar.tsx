import { useRef, useState } from "react";
import type { Editor } from "@tiptap/react";
import {
  applyCellAlign,
  applyCellBorderAll,
  applyCellDoubleUnderline,
  applyCellFill,
  captureSelection,
  currentCellAttrs,
  gridBorderValue,
  resetCellToTheme,
  restoreSelection,
  toggleCellBorderSide,
  BORDER_HIDDEN,
  BORDER_NONE,
  DEFAULT_BORDER_COLOR,
  FILL_NONE,
  type BorderSide,
  type CellAlign,
} from "../lib/cellFormatting";
import { indentBlocks, outdentBlocks } from "../lib/notesIndent";
import {
  HIGHLIGHT_COLORS,
  TEXT_COLORS,
  type PaletteSwatch,
} from "../lib/notesPalette";
import { pwc } from "../lib/theme";

const FILL_PRESETS: ReadonlyArray<{ label: string; color: string }> = [
  { label: "White", color: "#ffffff" },
  { label: "Grey", color: "#f4f4f4" },
  { label: "Highlight", color: "#fff6e5" },
];

const BORDER_COLOURS: ReadonlyArray<{ label: string; color: string }> = [
  { label: "Black", color: "#000000" },
  { label: "Grey", color: "#c9c9c9" },
  { label: "White", color: "#ffffff" },
  { label: "Orange", color: "#fd5108" },
  { label: "Blue", color: "#185fa5" },
];

const BORDER_SIDES: ReadonlyArray<{ side: BorderSide; label: string }> = [
  { side: "Top", label: "Top" },
  { side: "Right", label: "Right" },
  { side: "Bottom", label: "Bottom" },
  { side: "Left", label: "Left" },
];

/** Formatting controls for the currently focused TipTap notes editor. */
export function NotesEditorToolbar({ editor }: { editor: Editor }) {
  const [borderPaint, setBorderPaint] = useState(DEFAULT_BORDER_COLOR);
  const selectionRef = useRef<ReturnType<typeof captureSelection> | null>(null);
  const eraseActive = borderPaint === BORDER_HIDDEN;
  const paintValue = eraseActive ? BORDER_HIDDEN : gridBorderValue(borderPaint);

  const guarded = (run: () => void) => ({
    onMouseDown: (event: React.MouseEvent) => {
      selectionRef.current = captureSelection(editor);
      event.preventDefault();
    },
    onClick: () => {
      if (selectionRef.current) {
        try {
          restoreSelection(editor, selectionRef.current);
        } catch {
          // Structural table edits can invalidate a captured cell selection.
        }
      }
      run();
    },
  });

  const button = (
    label: React.ReactNode,
    ariaLabel: string,
    onClick: () => void,
    active = false,
  ) => (
    <button
      key={ariaLabel}
      type="button"
      aria-label={ariaLabel}
      title={ariaLabel}
      data-tooltip={ariaLabel}
      style={active ? styles.buttonActive : styles.button}
      {...guarded(onClick)}
    >
      {label}
    </button>
  );

  const group = (label: string, children: React.ReactNode) => (
    <div role="group" aria-label={label} title={label} style={styles.group}>
      <span aria-hidden="true" style={styles.groupLabel}>
        {label}
      </span>
      {children}
    </div>
  );

  const alignIcon = (align: "left" | "center" | "right") => (
    <span aria-hidden="true" style={{ ...styles.alignIcon, textAlign: align }}>
      ≡
    </span>
  );

  const swatch = (item: PaletteSwatch, kind: "text" | "highlight") => {
    const apply = () => {
      const chain = editor.chain().focus();
      if (kind === "text") {
        if (item.value === null) chain.unsetColor().run();
        else chain.setColor(item.value).run();
      } else if (item.value === null) chain.unsetHighlight().run();
      else chain.toggleHighlight({ color: item.value }).run();
    };
    const prefix = kind === "text" ? "Text colour" : "Highlight";
    return (
      <button
        key={`${kind}-${item.label}`}
        type="button"
        title={item.label}
        aria-label={`${prefix} ${item.label}`}
        data-tooltip={`${prefix} ${item.label}`}
        style={{ ...styles.swatch, background: item.value ?? pwc.white }}
        {...guarded(apply)}
      >
        {item.value === null ? "✕" : ""}
      </button>
    );
  };

  const sidePainted = (side: BorderSide) => {
    const value = currentCellAttrs(editor)?.[`border${side}`];
    return (
      typeof value === "string" &&
      value !== "" &&
      value !== BORDER_NONE &&
      value !== BORDER_HIDDEN
    );
  };

  return (
    <div style={styles.root} data-testid="editor-format-bar">
      <div role="toolbar" aria-label="Formatting" style={styles.row}>
        {group(
          "Text formatting",
          <>
            {button(
              <span style={{ fontWeight: 700 }}>B</span>,
              "Bold",
              () => editor.chain().focus().toggleBold().run(),
              editor.isActive("bold"),
            )}
            {button(
              <span style={{ fontStyle: "italic" }}>I</span>,
              "Italic",
              () => editor.chain().focus().toggleItalic().run(),
              editor.isActive("italic"),
            )}
            {button(
              <span style={{ textDecoration: "underline" }}>U</span>,
              "Underline",
              () => editor.chain().focus().toggleUnderline().run(),
              editor.isActive("underline"),
            )}
            {button(
              <span style={{ textDecoration: "line-through" }}>S</span>,
              "Strikethrough",
              () => editor.chain().focus().toggleStrike().run(),
              editor.isActive("strike"),
            )}
            {button(
              "x²",
              "Superscript",
              () => editor.chain().focus().toggleSuperscript().run(),
              editor.isActive("superscript"),
            )}
            {button(
              "x₂",
              "Subscript",
              () => editor.chain().focus().toggleSubscript().run(),
              editor.isActive("subscript"),
            )}
          </>,
        )}
        {group(
          "Text colour",
          TEXT_COLORS.map((item) => swatch(item, "text")),
        )}
        {group(
          "Highlight",
          HIGHLIGHT_COLORS.map((item) => swatch(item, "highlight")),
        )}
        {group(
          "Paragraph",
          <>
            {button(
              alignIcon("left"),
              "Align left",
              () => editor.chain().focus().setTextAlign("left").run(),
              editor.isActive({ textAlign: "left" }),
            )}
            {button(
              alignIcon("center"),
              "Align centre",
              () => editor.chain().focus().setTextAlign("center").run(),
              editor.isActive({ textAlign: "center" }),
            )}
            {button(
              alignIcon("right"),
              "Align right",
              () => editor.chain().focus().setTextAlign("right").run(),
              editor.isActive({ textAlign: "right" }),
            )}
            {button(
              "•≡",
              "Bullet list",
              () => editor.chain().focus().toggleBulletList().run(),
              editor.isActive("bulletList"),
            )}
            {button(
              "1≡",
              "Numbered list",
              () => editor.chain().focus().toggleOrderedList().run(),
              editor.isActive("orderedList"),
            )}
            {button(
              "H3",
              "Heading",
              () => editor.chain().focus().toggleHeading({ level: 3 }).run(),
              editor.isActive("heading", { level: 3 }),
            )}
            {button("⇤", "Decrease indent", () => outdentBlocks(editor))}
            {button("⇥", "Increase indent", () => indentBlocks(editor))}
            {button("▦", "Insert table", () =>
              editor
                .chain()
                .focus()
                .insertTable({ rows: 2, cols: 2, withHeaderRow: true })
                .run(),
            )}
          </>,
        )}
      </div>

      {editor.isActive("table") && (
        <div
          role="toolbar"
          aria-label="Table formatting"
          data-testid="table-format-bar"
          style={styles.tableRow}
        >
          {group(
            "Cell fill",
            <>
              {FILL_PRESETS.map((preset) =>
                button("■", `Fill ${preset.label}`, () =>
                  applyCellFill(editor, preset.color),
                ),
              )}
              {button("∅", "No fill", () => applyCellFill(editor, FILL_NONE))}
            </>,
          )}
          {group(
            "Borders",
            <>
              {BORDER_SIDES.map(({ side, label }) =>
                button(
                  side === "Top"
                    ? "▔"
                    : side === "Right"
                      ? "▕"
                      : side === "Bottom"
                        ? "▁"
                        : "▏",
                  `Border ${label}`,
                  () => toggleCellBorderSide(editor, side, paintValue),
                  sidePainted(side),
                ),
              )}
              {button("⊞", "Border all", () =>
                applyCellBorderAll(editor, paintValue),
              )}
              {button("⊠", "Border none", () =>
                applyCellBorderAll(editor, BORDER_HIDDEN),
              )}
              {button("═", "Double underline", () =>
                applyCellDoubleUnderline(editor),
              )}
            </>,
          )}
          {group(
            "Border colour",
            <>
              {BORDER_COLOURS.map(({ label, color }) => (
                <button
                  key={color}
                  type="button"
                  aria-label={`Border colour ${label}`}
                  aria-pressed={borderPaint === color}
                  title={`Use ${label.toLowerCase()} for the border buttons`}
                  data-tooltip={`Border colour ${label}`}
                  style={{
                    ...styles.swatch,
                    background: color,
                    outline:
                      borderPaint === color
                        ? `2px solid ${pwc.orange500}`
                        : "none",
                    outlineOffset: 1,
                  }}
                  {...guarded(() => setBorderPaint(color))}
                />
              ))}
              <button
                type="button"
                aria-label="Border colour erase"
                aria-pressed={eraseActive}
                title="Erase the chosen edge(s) — no line, not the grey grid"
                data-tooltip="Border colour erase"
                style={{
                  ...styles.swatch,
                  background: pwc.white,
                  outline: eraseActive ? `2px solid ${pwc.orange500}` : "none",
                  outlineOffset: 1,
                }}
                {...guarded(() => setBorderPaint(BORDER_HIDDEN))}
              >
                ✕
              </button>
            </>,
          )}
          {group(
            "Cell alignment",
            (["left", "center", "right"] as CellAlign[]).map((align) =>
              button(
                alignIcon(align),
                `Cell align ${align}`,
                () => applyCellAlign(editor, align),
                currentCellAttrs(editor)?.textAlign === align,
              ),
            ),
          )}
          {group(
            "Reset",
            button("↺", "Reset cell to theme", () => resetCellToTheme(editor)),
          )}
          {group(
            "Table structure",
            <>
              {button("▤↑", "Insert row above", () =>
                editor.chain().focus().addRowBefore().run(),
              )}
              {button("▤↓", "Insert row below", () =>
                editor.chain().focus().addRowAfter().run(),
              )}
              {button("▥←", "Insert column left", () =>
                editor.chain().focus().addColumnBefore().run(),
              )}
              {button("▥→", "Insert column right", () =>
                editor.chain().focus().addColumnAfter().run(),
              )}
              {button("⊞", "Merge cells", () =>
                editor.chain().focus().mergeCells().run(),
              )}
              {button("⊟", "Split cell", () =>
                editor.chain().focus().splitCell().run(),
              )}
              {button("━", "Toggle header row", () =>
                editor.chain().focus().toggleHeaderRow().run(),
              )}
              {button("▤−", "Delete row", () =>
                editor.chain().focus().deleteRow().run(),
              )}
              {button("▥−", "Delete column", () =>
                editor.chain().focus().deleteColumn().run(),
              )}
              {button("▦×", "Delete table", () =>
                editor.chain().focus().deleteTable().run(),
              )}
            </>,
          )}
        </div>
      )}
    </div>
  );
}

const styles = {
  root: { display: "flex", flexDirection: "column", gap: 4, marginTop: 4 },
  row: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 6,
    padding: "7px 8px",
    background: pwc.grey100,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 4,
  },
  tableRow: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 6,
    padding: "7px 8px",
    marginTop: 4,
    background: pwc.grey100,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 4,
  },
  group: {
    display: "inline-flex",
    alignItems: "center",
    gap: 3,
    padding: "3px 4px",
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 4,
  },
  groupLabel: {
    color: pwc.grey700,
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: 0.3,
    textTransform: "uppercase",
    whiteSpace: "nowrap",
    marginRight: 1,
  },
  alignIcon: { display: "inline-block", width: 13, lineHeight: 1 },
  swatch: {
    width: 28,
    height: 28,
    padding: 0,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: 3,
    cursor: "pointer",
    fontSize: 10,
    lineHeight: 1,
    color: pwc.grey700,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  },
  button: {
    minWidth: 28,
    height: 28,
    padding: "2px 5px",
    fontSize: 13,
    fontFamily: pwc.fontBody,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 3,
    color: pwc.grey700,
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  },
  buttonActive: {
    minWidth: 28,
    height: 28,
    padding: "2px 5px",
    fontSize: 13,
    fontFamily: pwc.fontBody,
    background: pwc.grey100,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: 3,
    color: pwc.grey900,
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  },
} satisfies Record<string, React.CSSProperties>;
