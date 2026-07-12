import { readFileSync } from "node:fs";
import { describe, test, expect } from "vitest";
import { pwc, tokens } from "../lib/theme";

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

// pwc token name -> the colour value index.css must contain
const INDEX_TOKENS = [
  "orange50", // form-control focus halo
  "grey50", // body bg, secondary/quiet-surface hover, row/tab hover
  "grey100", // quiet button hover
  "grey300", // secondary hover border + scrollbar thumb
  "grey500", // scrollbar thumb hover + interactive-card hover border
  "grey900", // body text, two-part focus outline, hover text
  "errorBg", // danger button hover background
  "errorText", // danger button hover border
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

  test("index.css primary-button hover uses the semantic action hover role", () => {
    expect(indexCss).toContain(tokens.color.action.primaryHover.toUpperCase());
  });

  test("index.css form-control focus border uses the semantic action role", () => {
    expect(indexCss).toContain(tokens.color.action.primary.toUpperCase());
  });

  test("interactive cards respond with border/surface, never a lift", () => {
    const cardHover = indexCss.slice(indexCss.indexOf(".PWC-CARD"));
    const hoverBlock = cardHover.slice(0, cardHover.indexOf("}") + 400);
    expect(hoverBlock).not.toContain("TRANSLATEY(-");
    expect(indexCss).not.toContain("TRANSFORM: TRANSLATEY(-2PX)");
  });
});
