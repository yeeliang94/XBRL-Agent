import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { pwc, tokens, component } from "../lib/theme";
import { ui } from "../lib/uiStyles";

function readReference(relativePath: string): string {
  const url = new URL(relativePath, import.meta.url);
  let path = decodeURIComponent(url.pathname).replace(/^\/@fs\//, "/");
  if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1);
  return readFileSync(path, "utf8");
}

const designSystem = readReference("../../../docs/xbrl-design-system.html");
const prototype = readReference("../../../docs/prototype-ui-overhaul.html");

describe("XBRL design system is the production authority", () => {
  test("declares the canonical production contract and Direction A", () => {
    expect(designSystem).toContain("CANONICAL PRODUCTION DESIGN SYSTEM");
    expect(designSystem).toContain("STATUS · PRODUCTION");
    expect(designSystem).toContain("DIRECTION · A");
    expect(prototype).toContain("Direction A — focused workspace");
  });

  test.each([
    ["orange700", "--orange-700"],
    ["orange500", "--orange-500"],
    ["orange400", "--orange-400"],
    ["orange300", "--orange-300"],
    ["orange200", "--orange-200"],
    ["orange100", "--orange-100"],
    ["orange50", "--orange-50"],
    ["grey500", "--grey-500"],
    ["grey400", "--grey-400"],
    ["grey300", "--grey-300"],
    ["grey200", "--grey-200"],
    ["grey100", "--grey-100"],
    ["grey50", "--grey-50"],
  ] as const)("pins %s to the canonical reference", (token, cssName) => {
    const declaration = `${cssName}: ${pwc[token].toLowerCase()}`;
    expect(designSystem.toLowerCase()).toContain(declaration);
    expect(prototype.toLowerCase()).toContain(declaration);
  });

  test("pins black and white to the prototype's ink and canvas aliases", () => {
    expect(designSystem.toLowerCase()).toContain(`--black: ${pwc.black.toLowerCase()}`);
    expect(designSystem.toLowerCase()).toContain(`--white: ${pwc.white.toLowerCase()}`);
    expect(prototype.toLowerCase()).toContain(`--ink: ${pwc.black.toLowerCase()}`);
    expect(prototype.toLowerCase()).toContain(`--canvas: ${pwc.white.toLowerCase()}`);
  });

  test("pins typography, spacing, radius, and flat-surface rules", () => {
    expect(pwc.fontBody).toBe('"Inter Variable", Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif');
    expect(pwc.fontMono).toBe('"SFMono-Regular", Consolas, "Liberation Mono", monospace');
    expect(pwc.radius).toMatchObject({ sm: 6, md: 8, lg: 12 });
    expect(pwc.space).toEqual({ xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48, xxxxl: 64 });
    expect(pwc.shadow.card).toBe("none");
    expect(pwc.shadow.elevated).toBe("0 14px 40px rgba(0, 0, 0, 0.12)");
    expect(ui.card.boxShadow).toBeUndefined();
  });
});

describe("Direction A semantic roles", () => {
  test("black carries actions while orange stays activity/attention", () => {
    expect(tokens.color.action.primary).toBe(pwc.black);
    expect(tokens.color.action.primaryHover).toBe(pwc.black);
    expect(component.button.primary.background).toBe(pwc.black);
    expect(tokens.color.brand.accent).toBe(pwc.orange500);
    expect(prototype).toContain(".shell-a .button.primary");
    expect(designSystem).toContain("Orange identifies activity and attention");
  });

  test("text and control roles match the exact XBRL values", () => {
    expect(tokens.color.text.primary).toBe(pwc.black);
    expect(tokens.color.text.body).toBe(pwc.black);
    expect(tokens.color.text.secondary).toBe("rgba(0, 0, 0, 0.64)");
    expect(tokens.color.text.muted).toBe("rgba(0, 0, 0, 0.46)");
    expect(tokens.color.border.control).toBe(pwc.grey500);
  });

  test("uses the three-level type scale and exact action geometry", () => {
    expect(ui.pageTitle.fontSize).toBe(28);
    expect(ui.sectionTitle.fontSize).toBe(16);
    expect(ui.bodyText.fontSize).toBe(14);
    expect(ui.buttonPrimary.minHeight).toBe(40);
    expect(ui.buttonPrimary.padding).toBe("0 15px");
    expect(ui.buttonSm.minHeight).toBe(34);
  });
});

describe("Direction A shell and responsive composition", () => {
  test("pins the 220px rail, 64px top bar, 1500px canvas, and manual 72px rail", () => {
    expect(ui.appShell.gridTemplateColumns).toBe("220px minmax(0, 1fr)");
    expect(ui.appTopbar.height).toBe(64);
    expect(tokens.layout.standard).toBe(1500);
    expect(tokens.layout.wideList).toBe(1500);
    expect(prototype).toContain(".shell-a { display: grid; grid-template-columns: 220px minmax(0,1fr)");
    expect(prototype).toContain(".a-topbar { height: 64px");
    expect(prototype).toContain("max-width: 1500px");
    expect(prototype).toContain(".shell-a.is-collapsed { grid-template-columns: 72px minmax(0,1fr)");
    expect(prototype).toContain('data-rail-toggle');
    expect(prototype).not.toContain('["review","notes"].includes(page) ? "review-mode"');
  });

  test("documents desktop, tablet, mobile, keyboard, and reduced-motion behavior", () => {
    for (const requirement of [
      "Large desktop:",
      "Standard desktop:",
      "Tablet:",
      "Mobile:",
      "Every icon-only control",
      "prefers-reduced-motion",
    ]) {
      expect(designSystem).toContain(requirement);
    }
    expect(prototype).toContain("@media (max-width: 1100px)");
    expect(prototype).toContain("@media (max-width: 780px)");
  });

  test("forbids line-based hover and selected-state indicators", () => {
    expect(designSystem).toContain("Do not use accent lines for hover, pressed, active or selected states");
    expect(prototype).toContain("Interactive states use surface, text and icon changes; never accent edge or underline indicators");
    expect(designSystem).not.toContain("box-shadow: inset 0 -3px 0 var(--orange-500)");
    expect(prototype).not.toContain("box-shadow: inset 0 -3px 0 var(--accent)");
  });

  test("pins the source-first Notes review composition", () => {
    expect(designSystem).toContain("the scout source-note inventory, the complete scrolling XBRL field list, and the source PDF");
    expect(designSystem).toContain("one-pixel visible Grey 100 rule");
    expect(designSystem).toContain("mount the rich-text editor only for the selected field");
    expect(designSystem).toContain("Put PDF paging, cited pages and zoom in one compact PDF toolbar");
    expect(prototype).toContain(".notes-three-pane { display: grid; grid-template-columns: 240px 9px minmax(410px, 1fr) 9px minmax(330px, 35%)");
    expect(prototype).toContain('aria-label="Notes sheet navigator"');
    expect(prototype).toContain('aria-label="Resize source notes"');
    expect(prototype).toContain("Sources (3)");
  });

  test("pins the Codex-style provider reasoning stream contract", () => {
    expect(designSystem).toContain("Merge current activity, tool actions and provider-returned reasoning");
    expect(designSystem).toContain("one flat <em>Live activity</em> sentence carousel");
    expect(designSystem).toContain("no event cards or nested activity panels");
    expect(designSystem).toContain("subtle older/newer controls");
    expect(designSystem).toContain("Never describe unavailable private chain-of-thought as exposed");
    expect(designSystem).toContain("frame-batch rapid reasoning updates");
    expect(prototype).toContain("Live activity");
    expect(prototype).toContain("1 / 8");
    expect(prototype).toContain("Provider-supplied");
  });
});
