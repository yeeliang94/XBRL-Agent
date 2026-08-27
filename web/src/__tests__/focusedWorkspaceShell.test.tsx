import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { TopNav } from "../components/TopNav";
import { ui } from "../lib/uiStyles";
import { pwc, tokens } from "../lib/theme";

const css = readFileSync("src/index.css", "utf8");
const appSource = readFileSync("src/App.tsx", "utf8");

describe("focused-workspace shell", () => {
  test("shared primitives define the persistent rail and context bar", () => {
    expect(ui.appShell.gridTemplateColumns).toBe("220px minmax(0, 1fr)");
    expect(ui.appShell.background).toBe(tokens.surface.canvas);
    expect(ui.appRail.background).toBe(tokens.surface.navigation);
    expect(ui.appRail.height).toBe("100vh");
    expect(ui.appTopbar.height).toBe(64);
    expect(ui.appTopbar.position).toBe("sticky");
    expect(tokens.surface.canvas).toBe(pwc.white);
    expect(tokens.surface.navigation).toBe(pwc.grey50);
  });

  test("App keeps a labelled navigation landmark, focused review mode, and skip target", () => {
    expect(appSource).toContain('aria-label="Workspace navigation"');
    expect(appSource).toContain("app-shell--review");
    expect(appSource).toContain('href="#main-content"');
    expect(appSource).toContain('id="main-content"');
    expect(appSource).toContain("reviewFocused && state.filename ? state.filename : contextLabel");
    expect(appSource).toContain("currentFilingTab={currentFilingTab}");
  });

  test("keeps logout in the top bar so it remains available on narrow screens", () => {
    const headerStart = appSource.indexOf('className="app-header-right"');
    const logout = appSource.indexOf('aria-label="Log out"');

    expect(headerStart).toBeGreaterThan(-1);
    expect(logout).toBeGreaterThan(headerStart);
  });

  test("top-level destinations remain links with stable URLs and current state", () => {
    render(<TopNav view="extract" onViewChange={() => {}} />);
    const queue = screen.getByRole("link", { name: "Work queue" });
    expect(queue).toHaveAttribute("href", "/");
    expect(queue).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("href", "/history");
  });

  test("responsive rules retain navigation and move evidence below instead of hiding it", () => {
    expect(css).toContain("@media (max-width: 780px)");
    expect(css).toContain("position: fixed !important");
    expect(css).toContain("inset: auto 0 0 0 !important");
    expect(css).toContain(".review-workspace .review-source-column");
    expect(css).toContain("flex: 1 0 100% !important");
    const tabletBlock = css.slice(css.indexOf("@media (max-width: 1000px)"), css.indexOf("@media (max-width: 780px)"));
    expect(tabletBlock).toContain("display: block !important");
    expect(tabletBlock).not.toContain(".review-source-column,\n  .review-workspace .review-resize-handle");
  });

  test("mobile review navigation stays horizontal with sticky identifiers and stacked note rows", () => {
    const mobileStart = css.indexOf("@media (max-width: 780px)");
    const mobileEnd = css.indexOf("@media (prefers-reduced-motion: reduce)", mobileStart);
    const mobile = css.slice(mobileStart, mobileEnd);
    expect(mobile).toContain(".review-workspace .review-menu-column");
    expect(mobile).toContain("flex-direction: row !important");
    expect(mobile).toContain("overflow-x: auto !important");
    expect(mobile).toContain(".concept-tree-row > .concept-tree-label");
    expect(mobile).toContain("position: sticky !important");
    expect(mobile).toContain(".notes-review-row");
    expect(mobile).toContain("grid-template-columns: minmax(0, 1fr) !important");
    expect(mobile).toContain("clip-path: inset(50%)");
    expect(mobile).not.toContain(".app-main-nav-label {\n    display: none");
    expect(mobile).toContain(".review-workspace .review-menu-column > :first-child");
    expect(mobile).toContain("flex: 0 0 auto !important");
    expect(mobile).toContain("min-height: 44px !important");
  });

  test("completed Activity uses the responsive roster-detail composition", () => {
    expect(css).toContain(".historical-agent-workspace");
    const tablet = css.slice(css.indexOf("@media (max-width: 1000px)"), css.indexOf("@media (max-width: 780px)"));
    expect(tablet).toContain("grid-template-columns: minmax(0, 1fr) !important");
    expect(tablet).toContain("border-top: 1px solid #EEEFF1 !important");
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
