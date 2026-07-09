import { describe, test, expect } from "vitest";
import {
  templateDisplayName,
  templateSubtitle,
  templateSortKey,
  notesSheetDisplayName,
  templatePickerLabel,
  templateGroupLabel,
} from "../lib/sheetLabels";

describe("templateDisplayName", () => {
  test("maps each face-statement variant to its short code", () => {
    expect(templateDisplayName("mfrs-company-sofp-cunoncu-v1")).toBe("SOFP");
    expect(templateDisplayName("mfrs-company-sopl-function-v1")).toBe("SOPL");
    expect(templateDisplayName("mfrs-company-soci-netoftax-v1")).toBe("SOCI");
    expect(templateDisplayName("mfrs-company-socf-indirect-v1")).toBe("SOCF");
    expect(templateDisplayName("mfrs-company-socie-v1")).toBe("SOCIE");
    expect(templateDisplayName("mpers-group-sore-v1")).toBe("SoRE");
  });

  test("does not confuse soci with socie (delimiter-guarded)", () => {
    // "-soci-" must not match inside "-socie-".
    expect(templateDisplayName("mfrs-group-socie-v1")).toBe("SOCIE");
  });

  test("falls back to the raw id for an unrecognised template", () => {
    expect(templateDisplayName("mfrs-company-mystery-v1")).toBe(
      "mfrs-company-mystery-v1",
    );
  });
});

describe("templateSubtitle", () => {
  test("glosses each statement code in plain English", () => {
    expect(templateSubtitle("mfrs-company-sofp-cunoncu-v1")).toBe("Balance sheet");
    expect(templateSubtitle("mfrs-company-sopl-function-v1")).toBe("Income statement");
    expect(templateSubtitle("mfrs-company-soci-netoftax-v1")).toBe("Comprehensive income");
    expect(templateSubtitle("mfrs-company-socie-v1")).toBe("Changes in equity");
    expect(templateSubtitle("mpers-group-sore-v1")).toBe("Retained earnings");
    expect(templateSubtitle("mfrs-company-socf-indirect-v1")).toBe("Cash flows");
  });

  test("returns null for an unrecognised template", () => {
    expect(templateSubtitle("mfrs-company-mystery-v1")).toBeNull();
  });
});

describe("templateSortKey", () => {
  test("orders statements in annual-report reading order", () => {
    const ids = [
      "mfrs-company-socf-indirect-v1",
      "mfrs-company-socie-v1",
      "mfrs-company-sofp-cunoncu-v1",
      "mfrs-company-soci-netoftax-v1",
      "mfrs-company-sopl-function-v1",
    ];
    const sorted = [...ids].sort((a, b) => templateSortKey(a) - templateSortKey(b));
    expect(sorted.map(templateDisplayName)).toEqual([
      "SOFP",
      "SOPL",
      "SOCI",
      "SOCIE",
      "SOCF",
    ]);
  });

  test("unrecognised templates sort last", () => {
    expect(templateSortKey("mfrs-company-mystery-v1")).toBeGreaterThan(
      templateSortKey("mfrs-company-socf-indirect-v1"),
    );
  });
});

describe("notesSheetDisplayName", () => {
  test("maps known notes sheets to plain English", () => {
    expect(notesSheetDisplayName("Notes-CI")).toBe("Corporate Information");
    expect(notesSheetDisplayName("Notes-SummaryofAccPol")).toBe(
      "Summary of Accounting Policies",
    );
    expect(notesSheetDisplayName("Notes-Listofnotes")).toBe("List of Notes");
    expect(notesSheetDisplayName("Notes-Issuedcapital")).toBe("Issued Capital");
    expect(notesSheetDisplayName("Notes-RelatedPartytran")).toBe(
      "Related Party Transactions",
    );
  });

  test("passes unknown sheet names through unchanged", () => {
    expect(notesSheetDisplayName("Notes-Future")).toBe("Notes-Future");
  });
});

describe("templatePickerLabel (D3)", () => {
  test("face statements read as CODE — Variant", () => {
    expect(templatePickerLabel("mfrs-company-sofp-orderofliquidity-v1")).toBe(
      "SOFP — Order of liquidity",
    );
    expect(templatePickerLabel("mfrs-group-socf-indirect-v1")).toBe(
      "SOCF — Indirect method",
    );
  });
  test("variant-less statements are just the code", () => {
    expect(templatePickerLabel("mfrs-company-socie-v1")).toBe("SOCIE");
    expect(templatePickerLabel("mpers-group-sore-v1")).toBe("SoRE");
  });
  test("notes templates read as Notes — Name", () => {
    expect(templatePickerLabel("mfrs-company-notes-issuedcapital-v1")).toBe(
      "Notes — Issued Capital",
    );
    expect(templatePickerLabel("mfrs-group-notes-corporateinfo-v1")).toBe(
      "Notes — Corporate Information",
    );
  });
  test("unparseable ids fall back to the raw id", () => {
    expect(templatePickerLabel("weird-id")).toBe("weird-id");
  });
});

describe("templateGroupLabel (D3)", () => {
  test("groups by standard and level", () => {
    expect(templateGroupLabel("mfrs-company-sofp-cunoncu-v1")).toBe("MFRS · Company");
    expect(templateGroupLabel("mpers-group-sore-v1")).toBe("MPERS · Group");
  });
  test("unparseable ids group under Other", () => {
    expect(templateGroupLabel("weird-id")).toBe("Other");
  });
});
