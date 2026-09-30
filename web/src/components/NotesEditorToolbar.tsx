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
import { pwc, tokens } from "../lib/theme";
import type { Glyph } from "./iconGlyphs";
import {
  AddColumnLeft,
  AddColumnRight,
  AddRowAbove,
  AddRowBelow,
  BorderAll,
  BorderBottom,
  BorderClear,
  BorderLeft,
  BorderRight,
  BorderTop,
  CellMerge,
  Delete,
  FormatAlignCenter,
  FormatAlignLeft,
  FormatAlignRight,
  FormatBold,
  FormatClear,
  FormatColorFill,
  FormatColorReset,
  FormatH3,
  FormatIndentDecrease,
  FormatIndentIncrease,
  FormatItalic,
  FormatListBulleted,
  FormatListNumbered,
  FormatUnderlined,
  PlaylistRemove,
  StrikethroughS,
  Subscript,
  Superscript,
  Table,
  Toolbar,
  VariableRemove,
  VerticalSplit,
} from "./iconGlyphs";

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
      className="pwc-btn-quiet"
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

  const icon = (G: Glyph) => <G size={20} />;
  const alignIcon = (align: "left" | "center" | "right") =>
    icon(align === "left" ? FormatAlignLeft : align === "center" ? FormatAlignCenter : FormatAlignRight);

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
        {item.value === null ? <FormatColorReset size={18} /> : null}
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
              icon(FormatBold),
              "Bold",
              () => editor.chain().focus().toggleBold().run(),
              editor.isActive("bold"),
            )}
            {button(
              icon(FormatItalic),
              "Italic",
              () => editor.chain().focus().toggleItalic().run(),
              editor.isActive("italic"),
            )}
            {button(
              icon(FormatUnderlined),
              "Underline",
              () => editor.chain().focus().toggleUnderline().run(),
              editor.isActive("underline"),
            )}
            {button(
              icon(StrikethroughS),
              "Strikethrough",
              () => editor.chain().focus().toggleStrike().run(),
              editor.isActive("strike"),
            )}
            {button(
              icon(Superscript),
              "Superscript",
              () => editor.chain().focus().toggleSuperscript().run(),
              editor.isActive("superscript"),
            )}
            {button(
              icon(Subscript),
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
              icon(FormatListBulleted),
              "Bullet list",
              () => editor.chain().focus().toggleBulletList().run(),
              editor.isActive("bulletList"),
            )}
            {button(
              icon(FormatListNumbered),
              "Numbered list",
              () => editor.chain().focus().toggleOrderedList().run(),
              editor.isActive("orderedList"),
            )}
            {button(
              icon(FormatH3),
              "Heading",
              () => editor.chain().focus().toggleHeading({ level: 3 }).run(),
              editor.isActive("heading", { level: 3 }),
            )}
            {button(icon(FormatIndentDecrease), "Decrease indent", () => outdentBlocks(editor))}
            {button(icon(FormatIndentIncrease), "Increase indent", () => indentBlocks(editor))}
            {button(icon(Table), "Insert table", () =>
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
                button(
                  <span style={{ display: "inline-flex", color: pwc.grey700, borderBottom: `4px solid ${preset.color}`, boxShadow: `0 1px 0 ${pwc.grey300}` }}>
                    <FormatColorFill size={18} />
                  </span>,
                  `Fill ${preset.label}`,
                  () => applyCellFill(editor, preset.color),
                ),
              )}
              {button(icon(FormatColorReset), "No fill", () => applyCellFill(editor, FILL_NONE))}
            </>,
          )}
          {group(
            "Borders",
            <>
              {BORDER_SIDES.map(({ side, label }) =>
                button(
                  icon(
                    side === "Top"
                      ? BorderTop
                      : side === "Right"
                        ? BorderRight
                        : side === "Bottom"
                          ? BorderBottom
                          : BorderLeft,
                  ),
                  `Border ${label}`,
                  () => toggleCellBorderSide(editor, side, paintValue),
                  sidePainted(side),
                ),
              )}
              {button(icon(BorderAll), "Border all", () =>
                applyCellBorderAll(editor, paintValue),
              )}
              {button(icon(BorderClear), "Border none", () =>
                applyCellBorderAll(editor, BORDER_HIDDEN),
              )}
              {button(<span style={{ fontSize: 16, fontWeight: 680, textDecoration: "underline double", textUnderlineOffset: 3 }}>U</span>, "Double underline", () =>
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
                <FormatColorReset size={18} />
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
            button(icon(FormatClear), "Reset cell to theme", () => resetCellToTheme(editor)),
          )}
          {group(
            "Table structure",
            <>
              {button(icon(AddRowAbove), "Insert row above", () =>
                editor.chain().focus().addRowBefore().run(),
              )}
              {button(icon(AddRowBelow), "Insert row below", () =>
                editor.chain().focus().addRowAfter().run(),
              )}
              {button(icon(AddColumnLeft), "Insert column left", () =>
                editor.chain().focus().addColumnBefore().run(),
              )}
              {button(icon(AddColumnRight), "Insert column right", () =>
                editor.chain().focus().addColumnAfter().run(),
              )}
              {button(icon(CellMerge), "Merge cells", () =>
                editor.chain().focus().mergeCells().run(),
              )}
              {button(icon(VerticalSplit), "Split cell", () =>
                editor.chain().focus().splitCell().run(),
              )}
              {button(icon(Toolbar), "Toggle header row", () =>
                editor.chain().focus().toggleHeaderRow().run(),
              )}
              {button(icon(PlaylistRemove), "Delete row", () =>
                editor.chain().focus().deleteRow().run(),
              )}
              {button(icon(VariableRemove), "Delete column", () =>
                editor.chain().focus().deleteColumn().run(),
              )}
              {button(icon(Delete), "Delete table", () =>
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
  root: { display: "flex", flexDirection: "column", gap: pwc.space.xs, marginTop: pwc.space.xs },
  row: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: pwc.space.sm,
    padding: pwc.space.sm,
    background: pwc.grey50,
    borderRadius: tokens.radius.control,
  },
  tableRow: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: pwc.space.sm,
    padding: pwc.space.sm,
    marginTop: pwc.space.xs,
    background: pwc.grey50,
    borderRadius: tokens.radius.control,
  },
  group: {
    display: "inline-flex",
    alignItems: "center",
    gap: 2,
    padding: "0 4px",
  },
  groupLabel: {
    color: tokens.color.text.secondary,
    fontSize: 12,
    fontWeight: pwc.weight.medium,
    whiteSpace: "nowrap",
    marginRight: pwc.space.xs,
  },
  swatch: {
    width: 28,
    height: 28,
    padding: 0,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
    color: tokens.color.icon.rest,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  },
  button: {
    minWidth: 34,
    height: 34,
    padding: "0 5px",
    background: "transparent",
    border: "1px solid transparent",
    borderRadius: tokens.radius.control,
    color: tokens.color.icon.rest,
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  },
  buttonActive: {
    minWidth: 34,
    height: 34,
    padding: "0 5px",
    background: pwc.white,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: tokens.radius.control,
    color: tokens.color.icon.strong,
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  },
} satisfies Record<string, React.CSSProperties>;
