import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";

// The HTML is the behavioural/visual specification; theme.ts and uiStyles.ts
// are the production implementation. This small contract catches the costly
// kind of drift (brand/foundation values) without snapshotting presentation-
// only markup in the documentation page.
const designSystemUrl = new URL("../../../docs/pwc-design-system.html", import.meta.url);
let designSystemPath = decodeURIComponent(designSystemUrl.pathname).replace(/^\/@fs\//, "/");
// URL pathnames retain a leading slash before a Windows drive letter.
if (/^\/[A-Za-z]:\//.test(designSystemPath)) designSystemPath = designSystemPath.slice(1);
const designSystem = readFileSync(designSystemPath, "utf8");

describe("design-system specification stays aligned with production tokens", () => {
  const colours = [
    "orange500",
    "orange700",
    "orange50",
    "grey50",
    "grey200",
    "grey300",
    "grey500",
    "grey800",
    "grey900",
    "success",
    "warning",
    "error",
    "info",
  ] as const;

  test.each(colours)("documents %s", (token) => {
    expect(designSystem.toUpperCase()).toContain(pwc[token].toUpperCase());
  });

  test("documents the production spacing and radius scales", () => {
    for (const value of Object.values(pwc.space)) {
      expect(designSystem).toContain(`${value}px`);
    }
    for (const value of Object.values(pwc.radius).filter((value) => value !== pwc.radius.pill)) {
      expect(designSystem).toContain(`${value}px`);
    }
  });

  test("documents semantic layout and financial-number roles", () => {
    expect(designSystem).toContain("ui.pageForm");
    expect(designSystem).toContain("ui.pageWide");
    expect(designSystem).toContain("font-variant-numeric:tabular-nums");
    expect(ui.financialValue.fontVariantNumeric).toBe("tabular-nums");
  });

  test("documents the shared motion contract", () => {
    expect(designSystem).toContain("pwc.motion.duration");
    expect(designSystem).toContain("pwc.motion.easing");
    expect(designSystem).toContain("prefers-reduced-motion");
  });
});
