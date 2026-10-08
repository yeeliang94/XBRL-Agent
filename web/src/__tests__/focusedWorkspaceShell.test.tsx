import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { TopNav } from "../components/TopNav";
import { ui } from "../lib/uiStyles";
import { pwc, tokens } from "../lib/theme";

const css = readFileSync("src/index.css", "utf8");
const notesCss = readFileSync("src/components/NotesReviewTab.css", "utf8");

describe("focused-workspace shell", () => {
  test("shared primitives define the compact context bar", () => {
    expect(ui.appTopbar.height).toBe(64);
    expect(ui.appTopbar.position).toBe("sticky");
    expect(tokens.surface.canvas).toBe(pwc.white);
    expect(tokens.surface.navigation).toBe(pwc.grey50);
  });

  test("top-level destinations remain links with stable URLs and current state", () => {
    render(<TopNav view="extract" onViewChange={() => {}} />);
    const queue = screen.getByRole("link", { name: "Work queue" });
    expect(queue).toHaveAttribute("href", "/");
    expect(queue).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "Runs" })).toBeNull();
  });

  test("responsive rules retain navigation and move evidence below instead of hiding it", () => {
    expect(css).toContain("@media (max-width: 780px)");
    expect(css).toContain(".review-workspace .review-source-column");
    expect(css).toContain("flex: 1 0 100% !important");
    const tabletBlock = css.slice(css.indexOf("@media (max-width: 1000px)"), css.indexOf("@media (max-width: 780px)"));
    expect(tabletBlock).toContain("display: block !important");
    expect(tabletBlock).not.toContain(".review-source-column,\n  .review-workspace .review-resize-handle");
    // JSDOM cannot evaluate container queries. Pin the explicit divider-width
    // contract here; browser QA checks actual visibility across the breakpoints.
    const narrowNotes = notesCss.slice(notesCss.indexOf("@container notes-review (max-width: 900px)"), notesCss.indexOf("@container notes-review (max-width: 640px)"));
    expect(narrowNotes).toContain("220px 9px minmax(0, 1fr)");
    expect(narrowNotes).not.toContain("display: none");
    expect(tabletBlock).not.toContain(".review-workspace .review-resize-handle {");
  });

  test("mobile navigation keeps sticky identifiers without overriding the note action grid", () => {
    const mobileStart = css.indexOf("@media (max-width: 780px)");
    const mobileEnd = css.indexOf("@media (prefers-reduced-motion: reduce)", mobileStart);
    const mobile = css.slice(mobileStart, mobileEnd);
    expect(mobile).toContain(".review-workspace .review-menu-column");
    expect(mobile).toContain("flex-direction: row !important");
    expect(mobile).toContain("overflow-x: auto !important");
    expect(mobile).toContain(".concept-tree-row > .concept-tree-label");
    expect(mobile).toContain("position: sticky !important");
    expect(mobile).not.toContain(".notes-review-row {");
    expect(mobile).toContain(".review-workspace .review-menu-column > :first-child");
    expect(mobile).toContain("flex: 0 0 auto !important");
    expect(mobile).toContain("min-height: 44px !important");
  });

  test("running and saved Activity share the responsive grouped composition", () => {
    expect(css).not.toContain(".historical-agent-workspace");
    const tablet = css.slice(css.indexOf("@media (max-width: 1100px)"), css.indexOf("@media (max-width: 1000px)"));
    expect(tablet).toContain(".multi-agent-workspace");
    expect(tablet).toContain("grid-template-columns: minmax(0, 1fr) !important");
    expect(tablet).toContain("border-top: 1px solid #DFE3E6 !important");
  });

  test("icon-only help appears on hover and keyboard focus with shared motion", () => {
    expect(css).toContain("[data-tooltip]::after");
    expect(css).toContain("[data-tooltip]:hover::after");
    expect(css).toContain("[data-tooltip]:focus-visible::after");
    expect(css).toContain("content: attr(data-tooltip)");
    expect(css).toContain("var(--motion-instant) var(--motion-standard)");
    expect(css).not.toContain("--motion-immediate");
  });
});
