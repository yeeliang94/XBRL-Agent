import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { pwc, tokens, component } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";

// The HTML is the behavioural/visual specification; theme.ts and uiStyles.ts
// are the production implementation. This contract catches the costly kind
// of drift (brand/foundation values, semantic roles, layout modes, status
// language) without snapshotting presentation-only markup in the
// documentation page.
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

describe("semantic token layer (tokens / component)", () => {
  test("accessible action roles are documented and independent of signature orange", () => {
    // The reviewed action values from the app-wide consistency plan.
    expect(tokens.color.action.primary).toBe("#C63D00");
    expect(tokens.color.action.primaryHover).toBe("#A83A00");
    expect(tokens.color.action.primary).not.toBe(pwc.orange500);
    expect(designSystem.toUpperCase()).toContain("#C63D00");
    expect(designSystem.toUpperCase()).toContain("#A83A00");
    // The spec names the semantic role, not just the hex.
    expect(designSystem).toContain("color.action.primary");
  });

  test("signature orange remains the brand indicator role", () => {
    expect(tokens.color.brand.accent).toBe(pwc.orange500);
    expect(component.nav.activeIndicator).toBe(pwc.orange500);
  });

  test("text roles map meaning to the neutral ladder", () => {
    expect(tokens.color.text.primary).toBe(pwc.grey900);
    expect(tokens.color.text.body).toBe(pwc.grey800);
    // Smallest READABLE role is grey700; grey500 is decorative/disabled only.
    expect(tokens.color.text.secondary).toBe(pwc.grey700);
    expect(tokens.color.text.muted).toBe(pwc.grey500);
    expect(designSystem).toContain("color.text.secondary");
  });

  test("component layer carries stable per-component decisions", () => {
    expect(component.button.primary.background).toBe(tokens.color.action.primary);
    expect(component.button.primary.backgroundHover).toBe(tokens.color.action.primaryHover);
    expect(component.table.header.surface).toBe(pwc.grey100);
    expect(component.dialog.scrim).toContain("rgba");
    expect(designSystem).toContain("button.primary.background.hover");
    expect(designSystem).toContain("table.header.surface");
    expect(designSystem).toContain("dialog.scrim");
  });
});

describe("canonical typography scale", () => {
  test("compact semantic roles carry the documented sizes", () => {
    expect(ui.pageTitle.fontSize).toBe(28);
    expect(ui.pageTitleCompact.fontSize).toBe(22);
    expect(ui.sectionTitle.fontSize).toBe(20);
    expect(ui.bodyText.fontSize).toBe(15);
    expect(ui.supportingText.fontSize).toBe(14);
    expect(ui.metadata.fontSize).toBe(13);
    expect(ui.microLabel.fontSize).toBe(11);
  });

  test("small text keeps readable contrast (grey700 or darker)", () => {
    expect(ui.supportingText.color).toBe(pwc.grey700);
    expect(ui.metadata.color).toBe(pwc.grey700);
    expect(ui.microLabel.color).toBe(pwc.grey700);
  });

  test("titles use semibold; body regular", () => {
    expect(ui.pageTitle.fontWeight).toBe(pwc.weight.semibold);
    expect(ui.bodyText.fontWeight).toBe(pwc.weight.regular);
  });
});

describe("canonical page layout modes", () => {
  test("the five task-based widths are defined once", () => {
    expect(tokens.layout.auth).toBe(380);
    expect(tokens.layout.form).toBe(840);
    expect(tokens.layout.standard).toBe(1120);
    expect(tokens.layout.wideList).toBe(1440);
    expect(ui.pageAuth.maxWidth).toBe(380);
    expect(ui.pageForm.maxWidth).toBe(840);
    expect(ui.pageStandard.maxWidth).toBe(1120);
    expect(ui.pageWide.maxWidth).toBe(1440);
    expect(ui.pageWorkspace.maxWidth).toBe("none");
  });
});

describe("monochrome status language", () => {
  test("the six canonical symbol families exist and are documented", () => {
    expect(STATUS_SYMBOLS.inProgress).toBe("○");
    expect(STATUS_SYMBOLS.success).toBe("✓");
    expect(STATUS_SYMBOLS.attention).toBe("!");
    expect(STATUS_SYMBOLS.failure).toBe("×");
    expect(STATUS_SYMBOLS.inactive).toBe("–");
    expect(STATUS_SYMBOLS.derived).toBe("◇");
    for (const symbol of Object.values(STATUS_SYMBOLS)) {
      expect(designSystem).toContain(symbol);
    }
  });

  test("the status primitive is neutral — no coloured dot, border, or fill", () => {
    expect(ui.status.color).toBe(pwc.grey800);
    expect(ui.statusSymbol.color).toBe(pwc.grey700);
    expect(ui.status.background).toBeUndefined();
    expect(ui.status.border).toBeUndefined();
    expect(ui.status.borderRadius).toBeUndefined();
  });
});

describe("cards and elevation", () => {
  test("static cards are flat (no shadow) and 8px radius", () => {
    expect(ui.card.boxShadow).toBeUndefined();
    expect(ui.card.borderRadius).toBe(8);
    expect(ui.borderedGroup.boxShadow).toBeUndefined();
    expect(ui.statTile.boxShadow).toBeUndefined();
  });

  test("elevation is reserved for genuine overlap", () => {
    expect(ui.dialog.boxShadow).toBe(pwc.shadow.modal);
    expect(ui.stickyActionBar.boxShadow).toBe(pwc.shadow.elevated);
  });
});

describe("tab geometry", () => {
  test("shared underline tab carries dark active text + orange indicator", () => {
    expect(ui.tab.color).toBe(pwc.grey700);
    expect(ui.tabActive.color).toBe(pwc.grey900);
    expect(ui.tabActive.borderBottom).toBe(`2px solid ${pwc.orange500}`);
    expect(ui.tabBar.borderBottom).toBe(`1px solid ${pwc.grey200}`);
  });
});

describe("page adoption matrix", () => {
  test("the specification carries the adoption matrix", () => {
    expect(designSystem).toContain("Page adoption status");
    for (const surface of [
      "App shell",
      "Login",
      "New extraction",
      "Runs (history list)",
      "Field labels",
      "Benchmarks",
      "Evaluation suites",
      "Settings",
      "Run report",
    ]) {
      expect(designSystem).toContain(surface);
    }
  });
});
