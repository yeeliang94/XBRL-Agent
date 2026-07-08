import { readFileSync } from "node:fs";
import { describe, test, expect } from "vitest";
import { pwc } from "../lib/theme";

// Read the CSS files at test time. Paths are relative to the vitest cwd (the
// web/ package root). `fs` is typed by src/node-shims.d.ts so this compiles
// under `tsc -b` without @types/node.

// Drift guard (peer-review MEDIUM). theme.ts is documented as the single
// token cascade point, but global interaction states (focus rings, hover,
// scrollbars, the rich-text editor) live in plain CSS that can't import the
// TS tokens. These files therefore duplicate the hex values by hand. This
// test pins each duplicated hex back to its theme token, so changing a token
// in theme.ts without updating the CSS fails here instead of drifting
// silently. If you intentionally retire a token from a CSS file, drop it from
// the matching list below.

const indexCss = readFileSync("src/index.css", "utf8").toUpperCase();
const notesCss = readFileSync("src/components/NotesReviewTab.css", "utf8").toUpperCase();

// token name -> the colour value the CSS file must contain
const INDEX_TOKENS = [
  "orange500", // focus ring border + :focus-visible
  "orange700", // primary button hover
  "orange50", // focus ring shadow + ghost hover
  "grey50", // body bg, secondary hover, row/tab hover
  "grey200", // subtle button hover
  "grey300", // secondary hover border + scrollbar thumb
  "grey500", // scrollbar thumb hover
  "grey900", // body text + agent-tab hover text
  "errorBg", // danger button hover background
  "error", // danger button hover border
] as const;

const NOTES_TOKENS = [
  "orange500", // editor focus border
  "orange50", // editor focus ring
  "grey100", // table header bg
  "grey300", // editor + table cell borders
  "grey800", // editor body text
  "grey900", // editor heading text
] as const;

describe("CSS interaction colours stay in sync with theme tokens", () => {
  test.each(INDEX_TOKENS)("index.css uses theme token %s", (token) => {
    expect(indexCss).toContain((pwc[token] as string).toUpperCase());
  });

  test.each(NOTES_TOKENS)("NotesReviewTab.css uses theme token %s", (token) => {
    expect(notesCss).toContain((pwc[token] as string).toUpperCase());
  });
});
