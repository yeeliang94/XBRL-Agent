import { describe, test, expect } from "vitest";
import { ui, uiClass } from "../lib/uiStyles";
import { pwc, tokens, component } from "../lib/theme";

// Pinning tests for the shared inline primitives. They assert the canonical
// metrics so a future component reaches for the primitive instead of
// re-deriving an off-spec value. Colours/spacing come from theme tokens, so
// these stay in lockstep with theme.

describe("ui.statTile — canonical KPI tile", () => {
  test("uses the 16px padding token, a hairline border, and stays flat", () => {
    expect(ui.statTile.padding).toBe(pwc.space.lg); // 16
    expect(ui.statTile.border).toBe(`1px solid ${pwc.grey200}`);
    expect(ui.statTile.borderRadius).toBe(pwc.radius.md);
    expect(ui.statTile.boxShadow).toBeUndefined();
  });
});

describe("ui.cardInset — dense inset box", () => {
  test("uses the 12px inset token", () => {
    expect(ui.cardInset.padding).toBe(pwc.space.md); // 12
  });
});

describe("ui.iconButton — shared glyph control", () => {
  test("meets the 32px minimum hit area", () => {
    expect(ui.iconButton.minWidth).toBeGreaterThanOrEqual(32);
    expect(ui.iconButton.minHeight).toBeGreaterThanOrEqual(32);
  });
});

describe("buttons — four roles, canonical geometry", () => {
  test("primary uses the accessible action role, not signature orange", () => {
    expect(ui.buttonPrimary.background).toBe(tokens.color.action.primary);
    expect(ui.buttonPrimary.background).not.toBe(pwc.orange500);
    expect(ui.buttonPrimary.color).toBe(pwc.white);
  });

  test("Subtle and Ghost converged into the single Quiet role", () => {
    expect(ui.buttonSubtle).toBe(ui.buttonQuiet);
    expect(ui.buttonGhost).toBe(ui.buttonQuiet);
    expect(uiClass.btnSubtle).toBe(uiClass.btnQuiet);
    expect(uiClass.btnGhost).toBe(uiClass.btnQuiet);
    expect(ui.buttonQuiet.background).toBe("transparent");
  });

  test("destructive stays quiet-outline with readable error text", () => {
    expect(ui.buttonDanger.color).toBe(pwc.errorText);
    expect(ui.buttonDanger.borderColor).toBe(pwc.errorText);
    expect(ui.buttonDanger.background).toBe(pwc.white);
  });

  test("default target 44px; compact 40px; nothing below WCAG 24px", () => {
    expect(ui.buttonPrimary.minHeight).toBe(44);
    expect(ui.buttonSm.minHeight).toBe(40);
    expect(ui.buttonLg.minHeight).toBe(48);
    expect(ui.iconButton.minHeight).toBeGreaterThanOrEqual(24);
  });

  test("buttons and inputs use the 6px control radius", () => {
    expect(ui.buttonPrimary.borderRadius).toBe(6);
    expect(ui.input.borderRadius).toBe(6);
    expect(ui.alertInfo.borderRadius).toBe(6);
  });
});

describe("ui.status — monochrome status primitive", () => {
  test("neutral symbol + explicit text; no colour, pill, or fill", () => {
    expect(ui.status.color).toBe(pwc.grey800);
    expect(ui.statusSymbol.color).toBe(pwc.grey700);
    expect(ui.status.background).toBeUndefined();
    expect(ui.status.borderRadius).toBeUndefined();
    expect(ui.statusSymbol.width).toBe(14);
  });
});

describe("ui.tab — shared underline tab", () => {
  test("carries the canonical geometry", () => {
    expect(ui.tab.padding).toBe("8px 16px");
    expect(ui.tab.borderBottom).toBe("2px solid transparent");
    expect(ui.tab.marginBottom).toBe(-1);
    expect(ui.tabActive.borderBottomColor).toBe(pwc.orange500);
    expect(uiClass.tab).toBe("pwc-tab");
  });
});

describe("ui.dialog / ui.scrim — shared modal primitives", () => {
  test("scrim uses the semantic component token and centers content", () => {
    expect(ui.scrim.background).toBe(component.dialog.scrim);
    expect(ui.scrim.position).toBe("fixed");
    expect(ui.scrim.inset).toBe(0);
  });

  test("dialog is the modal-elevation exception to flat surfaces", () => {
    expect(ui.dialog.boxShadow).toBe(pwc.shadow.modal);
    expect(ui.dialog.borderRadius).toBe(pwc.radius.lg);
  });

  test("action bar aligns actions to the decision region's end", () => {
    expect(ui.dialogActionBar.justifyContent).toBe("flex-end");
  });
});

describe("ui.borderedGroup — static grouping primitive", () => {
  test("bordered, flat, panel radius", () => {
    expect(ui.borderedGroup.border).toBe(`1px solid ${pwc.grey200}`);
    expect(ui.borderedGroup.borderRadius).toBe(pwc.radius.lg);
    expect(ui.borderedGroup.boxShadow).toBeUndefined();
  });
});

describe("table densities — header and body share the density", () => {
  test("standard density: 10px vertical rhythm (≈40px rows)", () => {
    expect(ui.th.padding).toBe(`10px ${pwc.space.lg}px`);
    expect(ui.td.padding).toBe(`10px ${pwc.space.lg}px`);
  });

  test("compact density: 8/12 (≈28–32px rows)", () => {
    expect(ui.thDense.padding).toBe(`${pwc.space.sm}px ${pwc.space.md}px`);
    expect(ui.tdDense.padding).toBe(`${pwc.space.sm}px ${pwc.space.md}px`);
  });

  test("comfortable density: 14/16 (≈48px rows)", () => {
    expect(ui.thComfortable.padding).toBe(`14px ${pwc.space.lg}px`);
    expect(ui.tdComfortable.padding).toBe(`14px ${pwc.space.lg}px`);
  });

  test("headers are sentence case — no tracked uppercase", () => {
    expect(ui.th.textTransform).toBeUndefined();
    expect(ui.thDense.textTransform).toBeUndefined();
    expect(ui.thComfortable.textTransform).toBeUndefined();
    expect(ui.th.color).toBe(pwc.grey700);
  });
});

describe("semantic design-system roles", () => {
  test("financial values use tabular numerals and right alignment", () => {
    expect(ui.financialValue.fontVariantNumeric).toBe("tabular-nums");
    expect(ui.financialValue.textAlign).toBe("right");
  });

  test("the five page modes carry the canonical widths", () => {
    expect(ui.pageAuth.maxWidth).toBe(380);
    expect(ui.pageForm.maxWidth).toBe(840);
    expect(ui.pageStandard.maxWidth).toBe(1120);
    expect(ui.pageWide.maxWidth).toBe(1440);
    expect(ui.pageWorkspace.maxWidth).toBe("none");
  });

  test("the sticky action bar is visually anchored", () => {
    expect(ui.stickyActionBar.position).toBe("sticky");
    expect(ui.stickyActionBar.bottom).toBe(0);
  });

  test("empty state offers a quiet divided region", () => {
    expect(ui.emptyState.borderTop).toBe(`1px solid ${pwc.grey200}`);
    expect(ui.emptyState.color).toBe(pwc.grey700);
  });
});
