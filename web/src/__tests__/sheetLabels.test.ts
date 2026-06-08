import { describe, test, expect } from "vitest";
import { templateDisplayName, notesSheetDisplayName } from "../lib/sheetLabels";

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
