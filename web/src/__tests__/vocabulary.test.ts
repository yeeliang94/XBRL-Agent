import { describe, test, expect } from "vitest";
import {
  TERMS,
  variantLabel,
  flagKindLabel,
  coverageStatusLabel,
  subNoteStateLabel,
  crossCheckLabel,
} from "../lib/vocabulary";

describe("TERMS", () => {
  test("exposes the renamed plain-English terms", () => {
    expect(TERMS.preScan).toBe("Document pre-scan");
    expect(TERMS.aiReview).toBe("AI review");
    expect(TERMS.figures).toBe("Figures");
    expect(TERMS.activity).toBe("Activity");
  });
});

describe("variantLabel", () => {
  test("renders every taxonomy variant code in plain English", () => {
    expect(variantLabel("CuNonCu")).toBe("Current / Non-current");
    expect(variantLabel("OrderOfLiquidity")).toBe("Order of liquidity");
    expect(variantLabel("Function")).toBe("By function");
    expect(variantLabel("Nature")).toBe("By nature");
    expect(variantLabel("BeforeTax")).toBe("Before tax");
    expect(variantLabel("NetOfTax")).toBe("Net of tax");
    expect(variantLabel("NotPrepared")).toBe("Not prepared");
    expect(variantLabel("Indirect")).toBe("Indirect method");
    expect(variantLabel("Direct")).toBe("Direct method");
  });
  test("unknown codes fall back to the code itself, never crash", () => {
    expect(variantLabel("Default")).toBe("Default");
    expect(variantLabel("Weird")).toBe("Weird");
  });
});

describe("flagKindLabel", () => {
  test("turns reviewer flag enums into readable labels", () => {
    expect(flagKindLabel("stuck")).toMatch(/couldn't resolve|needs your/i);
    expect(flagKindLabel("disputes_prior")).toMatch(/earlier figure|disagrees/i);
    expect(flagKindLabel("needs_human")).toMatch(/review/i);
  });
  test("never leaks the raw underscore enum", () => {
    expect(flagKindLabel("disputes_prior")).not.toContain("_");
  });
});

describe("coverageStatusLabel", () => {
  test("maps coverage statuses", () => {
    expect(coverageStatusLabel("placed")).toBe("Placed");
    expect(coverageStatusLabel("missing")).toBe("Missing");
    expect(coverageStatusLabel("skipped")).toBe("Skipped");
    expect(coverageStatusLabel("suspected_gap")).toMatch(/gap/i);
    expect(coverageStatusLabel("suspected_gap")).not.toContain("_");
  });
});

describe("subNoteStateLabel", () => {
  test("maps sub-note states to plain words", () => {
    expect(subNoteStateLabel("cited")).toBe("Mentioned");
    expect(subNoteStateLabel("not_verified")).toBe("Not checked");
    expect(subNoteStateLabel("verified")).toBe("Checked");
    expect(subNoteStateLabel("missing")).toBe("Missing");
  });
});

describe("crossCheckLabel", () => {
  test("humanises a snake_case check name without leaking underscores", () => {
    const out = crossCheckLabel("socie_to_sofp_equity");
    expect(out).not.toContain("_");
    expect(out.length).toBeGreaterThan(0);
  });
  test("unknown names still produce a readable phrase", () => {
    expect(crossCheckLabel("some_new_check")).not.toContain("_");
  });
});
