import { describe, test, expect } from "vitest";
import { pwc, tokens } from "../lib/theme";

// Contrast matrix (design-system Accessibility rule D / plan "Canonical
// Semantic Tokens"): the minimum ratios are encoded in ROLES, so this test
// computes real WCAG contrast for every supported foreground/background pair
// and state transition. If a token changes, the pair must still clear its
// documented floor — the failure message names the pair.
//
// Contracts:
//   - normal text: 4.5:1
//   - essential control boundaries and focus indicators: 3:1
//   - primary action text: 4.5:1 in every state (default + hover)
//   - disabled/decorative content is the only intentional low-contrast
//     exception (grey300 borders, grey500 muted text) and is not listed here.

function channel(hex: string, offset: number): number {
  const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
  return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
  const clean = hex.replace("#", "");
  return 0.2126 * channel(clean, 0) + 0.7152 * channel(clean, 2) + 0.0722 * channel(clean, 4);
}

export function contrastRatio(fg: string, bg: string): number {
  const [l1, l2] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
  return (l1 + 0.05) / (l2 + 0.05);
}

const SURFACES: Array<[string, string]> = [
  ["white", pwc.white],
  ["grey50 canvas", pwc.grey50],
  ["grey100 sunken", pwc.grey100],
];

describe("text roles meet 4.5:1 on every app surface", () => {
  const textRoles: Array<[string, string]> = [
    ["text.primary (grey900)", tokens.color.text.primary],
    ["text.body (grey800)", tokens.color.text.body],
    ["text.secondary (grey700)", tokens.color.text.secondary],
  ];

  for (const [roleName, fg] of textRoles) {
    for (const [surfaceName, bg] of SURFACES) {
      test(`${roleName} on ${surfaceName}`, () => {
        expect(contrastRatio(fg, bg)).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});

describe("primary action text meets 4.5:1 in every state", () => {
  test("white on action.primary (default)", () => {
    expect(contrastRatio(pwc.white, tokens.color.action.primary)).toBeGreaterThanOrEqual(4.5);
  });

  test("white on action.primaryHover (hover)", () => {
    expect(contrastRatio(pwc.white, tokens.color.action.primaryHover)).toBeGreaterThanOrEqual(4.5);
  });

  test("action.primary as small interactive text on white", () => {
    expect(contrastRatio(tokens.color.action.primary, pwc.white)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("status label text meets 4.5:1 on neutral surfaces", () => {
  const statusText: Array<[string, string]> = [
    ["successText", pwc.successText],
    ["errorText", pwc.errorText],
    ["warningText", pwc.warningText],
    ["infoText", pwc.infoText],
  ];

  for (const [name, fg] of statusText) {
    test(`${name} on white`, () => {
      expect(contrastRatio(fg, pwc.white)).toBeGreaterThanOrEqual(4.5);
    });
  }

  test("destructive hover keeps readable text (errorText on errorBg)", () => {
    expect(contrastRatio(pwc.errorText, pwc.errorBg)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("essential boundaries and focus meet 3:1", () => {
  test("control border (border.control) on white", () => {
    expect(contrastRatio(tokens.color.border.control, pwc.white)).toBeGreaterThanOrEqual(3);
  });

  test("focus.strong outline on every app surface", () => {
    for (const [, bg] of SURFACES) {
      expect(contrastRatio(tokens.color.focus.strong, bg)).toBeGreaterThanOrEqual(3);
    }
  });

  test("focus.ring (form-control focus border) on white", () => {
    expect(contrastRatio(tokens.color.focus.ring, pwc.white)).toBeGreaterThanOrEqual(3);
  });

  test("tab active indicator (brand) on white", () => {
    expect(contrastRatio(tokens.color.brand.indicator, pwc.white)).toBeGreaterThanOrEqual(3);
  });
});
