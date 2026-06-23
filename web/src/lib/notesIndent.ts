// Paragraph indentation for the notes editor. TipTap ships no first-party
// indent extension, so this is a small one: it adds an integer `indent` level
// attribute to paragraph + heading nodes, rendered as a `margin-left` in `em`,
// with increase/decrease helpers the toolbar calls. Persisted as inline
// `margin-left` the backend sanitiser accepts on `<p>/<h3>/<li>` in lock-step
// (notes/html_sanitize.py `_STYLE_PROPS_BY_TAG`). List nesting already handles
// list indentation via Tab; this covers plain paragraphs/headings.
import { Extension } from "@tiptap/core";
import type { Editor } from "@tiptap/react";

// One indent level = 2em; cap the depth so a stuck key can't run away.
export const INDENT_STEP_EM = 2;
export const MAX_INDENT_LEVEL = 8;
const INDENT_TYPES = ["paragraph", "heading"];

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
            // restores the same indentation. Tolerates "4em" / "4" / missing.
            parseHTML: (el: HTMLElement) => {
              const ml = parseFloat(el.style.marginLeft || "0");
              return Number.isFinite(ml) && ml > 0
                ? Math.round(ml / INDENT_STEP_EM)
                : 0;
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
        },
      },
    ];
  },
});

// Increase/decrease the indent level of every paragraph/heading touched by the
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
