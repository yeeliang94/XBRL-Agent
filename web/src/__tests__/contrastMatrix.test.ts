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
//   - focus indicators: 3:1
//   - primary action text: 4.5:1 in every state (default + hover)
//   - disabled/decorative content is the only intentional low-contrast
//     exception (grey300 borders) and is not listed here.

function channel(value8Bit: number): number {
  const value = value8Bit / 255;
  return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
}

function hexRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  return [
    parseInt(clean.slice(0, 2), 16),
    parseInt(clean.slice(2, 4), 16),
    parseInt(clean.slice(4, 6), 16),
  ];
}

function resolveRgb(color: string, background: string): [number, number, number] {
  if (color.startsWith("#")) return hexRgb(color);
  const match = color.match(/rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/);
  if (!match) throw new Error(`Unsupported test colour: ${color}`);
  const [, red, green, blue, alphaText] = match;
  const alpha = Number(alphaText);
  const bg = hexRgb(background);
  return [Number(red), Number(green), Number(blue)].map(
    (value, index) => value * alpha + bg[index] * (1 - alpha),
  ) as [number, number, number];
}

function luminance(rgb: [number, number, number]): number {
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

export function contrastRatio(fg: string, bg: string): number {
  const [l1, l2] = [luminance(resolveRgb(fg, bg)), luminance(resolveRgb(bg, bg))]
    .sort((a, b) => b - a);
  return (l1 + 0.05) / (l2 + 0.05);
}

const SURFACES: Array<[string, string]> = [
  ["white", pwc.white],
  ["grey50 canvas", pwc.grey50],
  ["grey100 sunken", pwc.grey100],
];

describe("text roles meet 4.5:1 on every app surface", () => {
  const textRoles: Array<[string, string]> = [
    ["text.primary (black)", tokens.color.text.primary],
    ["text.body (black)", tokens.color.text.body],
    ["text.secondary (64% black)", tokens.color.text.secondary],
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
  test("control border is distinguishable on white", () => {
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

describe("small coloured text roles meet 4.5:1", () => {
  test("grey500 on white", () => {
    expect(contrastRatio(pwc.grey500, pwc.white)).toBeGreaterThanOrEqual(4.5);
  });

  test("orange700 on white", () => {
    expect(contrastRatio(pwc.orange700, pwc.white)).toBeGreaterThanOrEqual(4.5);
  });
});
