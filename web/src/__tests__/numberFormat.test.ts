import { describe, test, expect } from "vitest";
import {
  formatCost,
  formatGroupedAccounting,
  parseAccountingInput,
} from "../lib/numberFormat";

describe("formatCost", () => {
  test("rounds to cents instead of 4 decimals", () => {
    expect(formatCost(2.8692)).toBe("$2.87");
    expect(formatCost(0.006)).toBe("$0.01");
  });
  test("sub-cent amounts show <$0.01 (not $0.00)", () => {
    expect(formatCost(0.0004)).toBe("<$0.01");
  });
  test("zero / null render as $0.00", () => {
    expect(formatCost(0)).toBe("$0.00");
    expect(formatCost(null)).toBe("$0.00");
  });
  test("groups thousands", () => {
    expect(formatCost(1234.5)).toBe("$1,234.50");
  });
});

describe("formatGroupedAccounting", () => {
  test("negatives use accounting parentheses, positives don't", () => {
    expect(formatGroupedAccounting("-20667")).toBe("(20,667)");
    expect(formatGroupedAccounting("20667")).toBe("20,667");
  });
  test("blank / half-typed input is left untouched", () => {
    expect(formatGroupedAccounting("")).toBe("");
    expect(formatGroupedAccounting("-")).toBe("-");
  });
});

describe("parseAccountingInput", () => {
  test("reads parentheses back as a negative and strips separators", () => {
    expect(parseAccountingInput("(20,667)")).toBe(-20667);
    expect(parseAccountingInput("1,234,567")).toBe(1234567);
    expect(parseAccountingInput("-2500")).toBe(-2500);
  });
  test("round-trips with formatGroupedAccounting", () => {
    for (const n of [-20667, 20667, 0, -1, 1234.5]) {
      expect(parseAccountingInput(formatGroupedAccounting(String(n)))).toBe(n);
    }
  });
});
