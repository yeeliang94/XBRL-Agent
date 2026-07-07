// Paragraph indentation for the notes editor. TipTap ships no first-party
// indent extension, so this is a small one: it adds an integer `indent` level
// attribute to paragraph + heading + list-item nodes, rendered as a `margin-left` in `em`,
// with increase/decrease helpers the toolbar calls. Persisted as inline
// `margin-left` the backend sanitiser accepts on `<p>/<h3>/<li>` in lock-step
// (notes/html_sanitize.py `_STYLE_PROPS_BY_TAG`). List nesting still handles
// structural list indentation via Tab; this attribute preserves an explicit
// pasted list-item offset without dropping it on the next edit.
import { Extension } from "@tiptap/core";
import type { Editor } from "@tiptap/react";

// One indent level = 2em; cap the depth so a stuck key can't run away.
export const INDENT_STEP_EM = 2;
export const MAX_INDENT_LEVEL = 8;
const INDENT_TYPES = ["paragraph", "heading", "listItem"];

// Phase 4 (Word-formatting fidelity): preserve a paragraph's before/after
// spacing (margin-top/margin-bottom) mirrored from a Word source. Unlike indent
// (a quantised level), these are raw lengths, so we parse + re-render the value
// verbatim. Bounded to a sane px/em so a junk value can't render off-screen; a
// non-matching value renders nothing (keeps the no-churn contract).
const MAX_SPACING_PX = 200;
function parseSpacing(raw: string): string | null {
  const v = (raw || "").trim().toLowerCase();
  const m = /^(\d+(?:\.\d+)?)(px|em)$/.exec(v);
  if (!m) return null;
  const num = parseFloat(m[1]);
  if (!Number.isFinite(num) || num <= 0) return null;
  const px = m[2] === "em" ? num * 16 : num;
  if (px > MAX_SPACING_PX) return null;
  return `${num}${m[2]}`;
}
function spacingAttribute(cssProp: "margin-top" | "margin-bottom") {
  const domProp = cssProp === "margin-top" ? "marginTop" : "marginBottom";
  return {
    default: null as string | null,
    parseHTML: (el: HTMLElement) =>
      parseSpacing((el.style as CSSStyleDeclaration)[domProp] || ""),
    renderHTML: (attrs: Record<string, unknown>) => {
      const key = cssProp === "margin-top" ? "spaceBefore" : "spaceAfter";
      const v = attrs[key];
      return typeof v === "string" && v ? { style: `${cssProp}: ${v}` } : {};
    },
  };
}

export const Indent = Extension.create({
  name: "notesIndent",
  addGlobalAttributes() {
    return [
      {
        types: INDENT_TYPES,
        attributes: {
          indent: {
            default: 0,
            // Parse the level back from the persisted margin-left so a reload
            // restores the same indentation. Convert units EXPLICITLY (a bare
            // `parseFloat` would read `16px` as 16 em → level 8, and an
            // oversized value would render off-screen), then CLAMP to the
            // allowed range. `em` is our own output; `px` is converted
            // (1em ≈ 16px); anything else is treated as un-indented.
            parseHTML: (el: HTMLElement) => {
              const raw = (el.style.marginLeft || "").trim().toLowerCase();
              const num = parseFloat(raw);
              if (!Number.isFinite(num) || num <= 0) return 0;
              let em: number;
              if (raw.endsWith("em")) em = num;
              else if (raw.endsWith("px")) em = num / 16;
              else return 0;
              const level = Math.round(em / INDENT_STEP_EM);
              return Math.min(MAX_INDENT_LEVEL, Math.max(0, level));
            },
            // Render only when indented, so an un-indented paragraph stays
            // style-free (and the no-churn contract isn't perturbed).
            renderHTML: (attrs: Record<string, unknown>) => {
              const level = Number(attrs.indent) || 0;
              return level > 0
                ? { style: `margin-left: ${level * INDENT_STEP_EM}em` }
                : {};
            },
          },
          // margin-top / margin-bottom preserved verbatim (Phase 4). TipTap's
          // mergeAttributes concatenates the `style` these contribute with the
          // indent's margin-left, so all three round-trip together.
          spaceBefore: spacingAttribute("margin-top"),
          spaceAfter: spacingAttribute("margin-bottom"),
        },
      },
    ];
  },
});

// Increase/decrease the indent level of every supported block touched by the
// selection. Uses `.command()` (not a registered command name) to avoid the
// TS module-augmentation boilerplate; mutates the transaction only when
// dispatching, per TipTap's can-vs-do convention.
function adjustIndent(editor: Editor, delta: 1 | -1): boolean {
  return editor
    .chain()
    .focus()
    .command(({ state, tr, dispatch }) => {
      const { from, to } = state.selection;
      let changed = false;
      state.doc.nodesBetween(from, to, (node, pos) => {
        if (!INDENT_TYPES.includes(node.type.name)) return;
        const current = Number(node.attrs.indent) || 0;
        const next = Math.min(MAX_INDENT_LEVEL, Math.max(0, current + delta));
        if (next === current) return;
        changed = true;
        if (dispatch) {
          tr.setNodeMarkup(pos, undefined, { ...node.attrs, indent: next });
        }
      });
      return changed;
    })
    .run();
}

export const indentBlocks = (editor: Editor): boolean => adjustIndent(editor, 1);
export const outdentBlocks = (editor: Editor): boolean => adjustIndent(editor, -1);
