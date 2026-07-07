// Unit tests for the custom paragraph-indent extension (notes editor v2).
// There is no first-party TipTap indent extension, so this pins our own:
// indent/outdent adjust an `indent` level attribute that renders as
// `margin-left` (em) and parses back on reload, clamped to [0, MAX].
import { describe, it, expect } from "vitest";
import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import {
  Indent,
  indentBlocks,
  outdentBlocks,
  INDENT_STEP_EM,
  MAX_INDENT_LEVEL,
} from "../lib/notesIndent";

function makeEditor(html: string): Editor {
  return new Editor({
    extensions: [
      StarterKit.configure({
        code: false,
        codeBlock: false,
        blockquote: false,
        horizontalRule: false,
      }),
      Indent,
    ],
    content: html,
  });
}

function firstParaIndent(editor: Editor): number {
  let indent = -1;
  editor.state.doc.descendants((node) => {
    if (indent === -1 && node.type.name === "paragraph") {
      indent = Number(node.attrs.indent) || 0;
      return false;
    }
    return true;
  });
  return indent;
}

function firstListItemIndent(editor: Editor): number {
  let indent = -1;
  editor.state.doc.descendants((node) => {
    if (indent === -1 && node.type.name === "listItem") {
      indent = Number(node.attrs.indent) || 0;
      return false;
    }
    return true;
  });
  return indent;
}

describe("notesIndent", () => {
  it("indentBlocks increases the level and renders margin-left", () => {
    const editor = makeEditor("<p>hello</p>");
    indentBlocks(editor);
    expect(firstParaIndent(editor)).toBe(1);
    expect(editor.getHTML()).toContain(`margin-left: ${INDENT_STEP_EM}em`);
    editor.destroy();
  });

  it("outdentBlocks decreases and clamps at 0 (no negative indent)", () => {
    const editor = makeEditor("<p>hello</p>");
    indentBlocks(editor);
    indentBlocks(editor); // level 2
    outdentBlocks(editor); // level 1
    expect(firstParaIndent(editor)).toBe(1);
    outdentBlocks(editor); // level 0
    outdentBlocks(editor); // stays 0 — no negative
    expect(firstParaIndent(editor)).toBe(0);
    // Un-indented paragraph is style-free (no margin-left).
    expect(editor.getHTML()).not.toContain("margin-left");
    editor.destroy();
  });

  it("caps the indent at MAX_INDENT_LEVEL", () => {
    const editor = makeEditor("<p>hello</p>");
    for (let i = 0; i < MAX_INDENT_LEVEL + 5; i++) indentBlocks(editor);
    expect(firstParaIndent(editor)).toBe(MAX_INDENT_LEVEL);
    editor.destroy();
  });

  it("parses an existing margin-left back into the level on load", () => {
    const editor = makeEditor(
      `<p style="margin-left: ${INDENT_STEP_EM * 3}em">x</p>`,
    );
    expect(firstParaIndent(editor)).toBe(3);
    editor.destroy();
  });

  it("preserves an explicit pasted list-item indent on reload", () => {
    const editor = makeEditor(
      `<ul><li style="margin-left: ${INDENT_STEP_EM * 2}em">x</li></ul>`,
    );
    expect(firstListItemIndent(editor)).toBe(2);
    expect(editor.getHTML()).toContain(
      `margin-left: ${INDENT_STEP_EM * 2}em`,
    );
    editor.destroy();
  });

  it("converts a px margin-left rather than reading it as em", () => {
    // 16px ≈ 1em → ~level 1, NOT level 8 (the bug: parseFloat('16px')/2 = 8).
    const editor = makeEditor('<p style="margin-left: 16px">x</p>');
    expect(firstParaIndent(editor)).toBeLessThanOrEqual(1);
    editor.destroy();
  });

  it("clamps an oversized parsed indent to MAX_INDENT_LEVEL (em and px)", () => {
    const big = makeEditor('<p style="margin-left: 100em">x</p>');
    expect(firstParaIndent(big)).toBe(MAX_INDENT_LEVEL);
    big.destroy();
    const bigPx = makeEditor('<p style="margin-left: 1000px">x</p>');
    expect(firstParaIndent(bigPx)).toBe(MAX_INDENT_LEVEL);
    bigPx.destroy();
  });

  // --- Phase 4: paragraph before/after spacing round-trip -------------------
  function firstParaAttrs(editor: Editor): Record<string, unknown> {
    let attrs: Record<string, unknown> = {};
    editor.state.doc.descendants((node) => {
      if (node.type.name === "paragraph") {
        attrs = node.attrs as Record<string, unknown>;
        return false;
      }
      return true;
    });
    return attrs;
  }

  it("preserves margin-top/margin-bottom (Word-source spacing) on reload", () => {
    const editor = makeEditor(
      '<p style="margin-top: 6px; margin-bottom: 13px">x</p>',
    );
    const attrs = firstParaAttrs(editor);
    expect(attrs.spaceBefore).toBe("6px");
    expect(attrs.spaceAfter).toBe("13px");
    const html = editor.getHTML();
    expect(html).toContain("margin-top: 6px");
    expect(html).toContain("margin-bottom: 13px");
    editor.destroy();
  });

  it("renders spacing alongside indent without clobbering either", () => {
    const editor = makeEditor(
      `<p style="margin-left: ${INDENT_STEP_EM}em; margin-bottom: 8px">x</p>`,
    );
    const html = editor.getHTML();
    expect(html).toContain(`margin-left: ${INDENT_STEP_EM}em`);
    expect(html).toContain("margin-bottom: 8px");
    editor.destroy();
  });

  it("ignores a junk / oversized spacing value (no-churn contract)", () => {
    const editor = makeEditor('<p style="margin-top: 9999px">x</p>');
    expect(firstParaAttrs(editor).spaceBefore).toBeNull();
    editor.destroy();
  });
});
