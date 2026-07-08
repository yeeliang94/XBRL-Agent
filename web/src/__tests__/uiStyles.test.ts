import { describe, test, expect } from "vitest";
import { ui } from "../lib/uiStyles";
import { pwc } from "../lib/theme";

// Pinning tests for the shared inline primitives introduced by the layout
// normalization (Phase 8). They assert the canonical metrics so a future
// component reaches for the primitive instead of re-deriving an off-spec value.
// Colours/spacing come from theme tokens, so these stay in lockstep with theme.

describe("ui.statTile — canonical KPI tile", () => {
  test("uses the 16px padding token and a hairline border", () => {
    expect(ui.statTile.padding).toBe(pwc.space.lg); // 16
    expect(ui.statTile.border).toBe(`1px solid ${pwc.grey200}`);
    expect(ui.statTile.borderRadius).toBe(pwc.radius.md);
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

describe("ui.thDense / ui.tdDense — one dense table variant", () => {
  test("cells use the 8/12 dense padding", () => {
    expect(ui.tdDense.padding).toBe(`${pwc.space.sm}px ${pwc.space.md}px`);
    expect(ui.thDense.padding).toBe(`${pwc.space.sm}px ${pwc.space.md}px`);
  });
});
