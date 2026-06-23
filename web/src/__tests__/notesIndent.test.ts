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
});
