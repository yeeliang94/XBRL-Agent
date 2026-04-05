import { describe, test, expect } from "vitest";
import { pwc } from "../lib/theme";

describe("PwC theme tokens", () => {
  test("exports all required color tokens", () => {
    // Primary
    expect(pwc.black).toBe("#000000");
    expect(pwc.white).toBe("#FFFFFF");
    expect(pwc.orange500).toBe("#FD5108");
    expect(pwc.orange700).toBe("#C52B09");
    expect(pwc.orange400).toBe("#FE7C39");
    expect(pwc.orange100).toBe("#FFE8D4");
    expect(pwc.orange50).toBe("#FFF5ED");

    // Greys
    expect(pwc.grey50).toBeDefined();
    expect(pwc.grey100).toBeDefined();
    expect(pwc.grey200).toBeDefined();
    expect(pwc.grey300).toBeDefined();
    expect(pwc.grey500).toBeDefined();
    expect(pwc.grey700).toBeDefined();
    expect(pwc.grey800).toBeDefined();
    expect(pwc.grey900).toBeDefined();

    // Semantic
    expect(pwc.success).toBe("#16A34A");
    expect(pwc.error).toBe("#DC2626");
    expect(pwc.thinking).toBe("#7C3AED");
  });

  test("exports typography, spacing, radius, shadow tokens", () => {
    // Typography
    expect(pwc.fontHeading).toContain("Arial");
    expect(pwc.fontBody).toContain("Arial");
    expect(pwc.fontMono).toContain("SF Mono");

    // Spacing
    expect(pwc.space.xs).toBe(4);
    expect(pwc.space.sm).toBe(8);
    expect(pwc.space.md).toBe(12);
    expect(pwc.space.lg).toBe(16);
    expect(pwc.space.xl).toBe(24);
    expect(pwc.space.xxl).toBe(32);

    // Radius
    expect(pwc.radius.sm).toBe(4);
    expect(pwc.radius.md).toBe(8);
    expect(pwc.radius.lg).toBe(12);

    // Shadows
    expect(pwc.shadow.card).toBeDefined();
    expect(pwc.shadow.elevated).toBeDefined();
    expect(pwc.shadow.modal).toBeDefined();
  });

  test("all color values are valid hex strings", () => {
    const hexPattern = /^#[0-9A-Fa-f]{6}$/;
    const colorKeys = [
      "black",
      "white",
      "orange500",
      "orange700",
      "orange400",
      "orange100",
      "orange50",
      "grey50",
      "grey100",
      "grey200",
      "grey300",
      "grey500",
      "grey700",
      "grey800",
      "grey900",
      "success",
      "error",
      "thinking",
    ] as const;

    for (const key of colorKeys) {
      expect(pwc[key], `${key} should be valid hex`).toMatch(hexPattern);
    }
  });
});
