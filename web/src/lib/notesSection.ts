import { Node } from "@tiptap/core";

// A source-backed sub-note owns its heading and every following block until
// the next peer section. The wrapper is semantic; the shared indent is applied
// in review CSS and on the two mTool transport paths.
export const NotesSection = Node.create({
  name: "notesSection",
  group: "block",
  content: "block+",
  defining: true,
  parseHTML() {
    return [{ tag: 'div[data-note-section="1"]' }];
  },
  renderHTML() {
    return ["div", { "data-note-section": "1" }, 0];
  },
});
