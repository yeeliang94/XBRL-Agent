// Phase 1 tests for the shared tool-label module. These cover both extraction
// and scout tools so live, scout pre-run, and history replay all render the
// same wording through a single module.
import { describe, test, expect } from "vitest";
import { humanToolName, argsPreview, resultSummary } from "../lib/toolLabels";

describe("humanToolName", () => {
  // Extraction tools — the five tools registered on the extraction agent.
  test("read_template → 'Reading template'", () => {
    expect(humanToolName("read_template")).toBe("Reading template");
  });
  test("view_pdf_pages → 'Checking PDF pages'", () => {
    expect(humanToolName("view_pdf_pages")).toBe("Checking PDF pages");
  });
  test("fill_workbook → 'Filling workbook'", () => {
    expect(humanToolName("fill_workbook")).toBe("Filling workbook");
  });
  test("verify_totals → 'Verifying totals'", () => {
    expect(humanToolName("verify_totals")).toBe("Verifying totals");
  });
  test("save_result → 'Saving result'", () => {
    expect(humanToolName("save_result")).toBe("Saving result");
  });

  // Scout tools — the six tools on the scout agent.
  test("find_toc → 'Locating table of contents'", () => {
    expect(humanToolName("find_toc")).toBe("Locating table of contents");
  });
  test("view_pages → 'Checking PDF pages'", () => {
    // Same wording as view_pdf_pages so scout and extraction look identical.
    expect(humanToolName("view_pages")).toBe("Checking PDF pages");
  });
  test("parse_toc_text → 'Reading table of contents'", () => {
    expect(humanToolName("parse_toc_text")).toBe("Reading table of contents");
  });
  test("check_variant_signals → 'Checking variant signals'", () => {
    expect(humanToolName("check_variant_signals")).toBe("Checking variant signals");
  });
  test("discover_notes → 'Discovering notes'", () => {
    expect(humanToolName("discover_notes")).toBe("Discovering notes");
  });
  test("save_infopack → 'Saving scout results'", () => {
    expect(humanToolName("save_infopack")).toBe("Saving scout results");
  });

  // Fallback — unknown tools become title-cased.
  test("unknown tool falls back to title-cased name", () => {
    expect(humanToolName("some_unknown_tool")).toBe("Some Unknown Tool");
  });
});

describe("argsPreview", () => {
  // view_pdf_pages — English-style page lists with an Oxford 'and' before the
  // last item. Collapses to a range summary once the list is long.
  test("view_pdf_pages with one page → 'page 5'", () => {
    expect(argsPreview("view_pdf_pages", { pages: [5] })).toBe("page 5");
  });
  test("view_pdf_pages with two pages → 'pages 12 and 13'", () => {
    expect(argsPreview("view_pdf_pages", { pages: [12, 13] })).toBe("pages 12 and 13");
  });
  test("view_pdf_pages with four pages → 'pages 3, 4, 5 and 6'", () => {
    expect(argsPreview("view_pdf_pages", { pages: [3, 4, 5, 6] })).toBe("pages 3, 4, 5 and 6");
  });
  test("view_pdf_pages with 5+ consecutive pages → 'N pages (first–last)'", () => {
    // Uses en-dash (U+2013), not hyphen-minus.
    expect(argsPreview("view_pdf_pages", { pages: [1, 2, 3, 4, 5, 6, 7] }))
      .toBe("7 pages (1\u20137)");
  });
  test("view_pdf_pages with 5+ NON-consecutive pages stays in list form (no range collapse)", () => {
    // Peer-review fix: range form would lie about which pages were scanned.
    expect(argsPreview("view_pdf_pages", { pages: [1, 7, 15, 23, 45, 67] }))
      .toBe("pages 1, 7, 15, 23, 45 and 67");
  });
  test("view_pages (scout) shares the exact same formatter as view_pdf_pages", () => {
    expect(argsPreview("view_pages", { pages: [12, 13] })).toBe("pages 12 and 13");
  });

  // fill_workbook — collapses the field list to "N fields → Sheet".
  test("fill_workbook with 24 fields on SOFP-Sub-CuNonCu → '24 fields → SOFP-Sub-CuNonCu'", () => {
    const fields = Array.from({ length: 24 }, (_, i) => ({
      sheet: "SOFP-Sub-CuNonCu",
      field_label: `Field ${i}`,
      col: 2,
      value: i,
    }));
    const args = { fields_json: JSON.stringify({ fields }) };
    expect(argsPreview("fill_workbook", args)).toBe("24 fields → SOFP-Sub-CuNonCu");
  });

  // read_template — shows just the filename, not the full path.
  test("read_template with a full path → filename only", () => {
    const args = { path: "/x/y/01-SOFP-CuNonCu.xlsx" };
    expect(argsPreview("read_template", args)).toBe("01-SOFP-CuNonCu.xlsx");
  });

  // parse_toc_text — a short excerpt (~40 chars) so the row stays tight.
  test("parse_toc_text renders a short text excerpt", () => {
    const args = { text: "Statement of Financial Position 5 ... other content that would overflow" };
    const preview = argsPreview("parse_toc_text", args);
    // Leading chars preserved; length bounded.
    expect(preview.startsWith("Statement of Financial Position")).toBe(true);
    expect(preview.length).toBeLessThanOrEqual(44); // 40 + possible trailing ellipsis
  });

  // discover_notes — just a hint that we read from the face page text.
  test("discover_notes with face_text shows 'from face page'", () => {
    const args = { face_text: "Note 5. Property, plant and equipment ..." };
    expect(argsPreview("discover_notes", args)).toBe("from face page");
  });

  // Tools without meaningful args return an empty string so the row stays
  // uncluttered.
  test.each([
    ["find_toc"],
    ["save_infopack"],
    ["save_result"],
    ["verify_totals"],
  ])("%s with no meaningful args → empty string", (toolName) => {
    expect(argsPreview(toolName, {})).toBe("");
  });
});

describe("resultSummary", () => {
  test("fill_workbook 'wrote 24 fields' → success / '24 values'", () => {
    expect(resultSummary("fill_workbook", "wrote 24 fields")).toEqual({
      text: "24 values",
      tone: "success",
    });
  });
  test("verify_totals with 'Balanced: True' → success / 'balanced'", () => {
    const summary = "Balanced: True\nMatches PDF: True\nComputed totals: {}";
    expect(resultSummary("verify_totals", summary)).toEqual({
      text: "balanced",
      tone: "success",
    });
  });
  test("verify_totals with 'Balanced: False' → warn / 'mismatch'", () => {
    const summary = "Balanced: False\nMatches PDF: False\nComputed totals: {}";
    expect(resultSummary("verify_totals", summary)).toEqual({
      text: "mismatch",
      tone: "warn",
    });
  });
  test("find_toc with '12 entries' somewhere in the summary → success / '12 entries'", () => {
    const summary = '{"entries": [..12 entries..]}';
    expect(resultSummary("find_toc", summary)).toEqual({
      text: "12 entries",
      tone: "success",
    });
  });
  test("find_toc with REAL str(dict) output counts 'name' keys", () => {
    // Peer-review fix: the real scout find_toc passes through
    // coordinator.py's str(content)[:800], producing Python repr like:
    //   {'toc_page': 1, 'candidate_pages': [1, 2], 'entries': [
    //     {'name': 'SOFP', 'type': 'SOFP', 'page': 5},
    //     {'name': 'SOPL', 'type': 'SOPL', 'page': 8},
    //     {'name': 'SOCIE', 'type': 'SOCIE', 'page': 12},
    //   ]}
    // The earlier regex never matched this; counting 'name' keys does.
    const summary =
      "{'toc_page': 1, 'candidate_pages': [1, 2], 'entries': [" +
      "{'name': 'SOFP', 'type': 'SOFP', 'page': 5}, " +
      "{'name': 'SOPL', 'type': 'SOPL', 'page': 8}, " +
      "{'name': 'SOCIE', 'type': 'SOCIE', 'page': 12}]}";
    expect(resultSummary("find_toc", summary)).toEqual({
      text: "3 entries",
      tone: "success",
    });
  });
  test("find_toc with zero entries → null (no misleading '0 entries' badge)", () => {
    const summary = "{'toc_page': 1, 'candidate_pages': [], 'entries': []}";
    expect(resultSummary("find_toc", summary)).toBeNull();
  });
  test("save_infopack with any non-empty summary → success / 'saved'", () => {
    expect(resultSummary("save_infopack", "ok")).toEqual({
      text: "saved",
      tone: "success",
    });
  });
  test("unknown tool / unparseable summary → null (caller falls back to duration)", () => {
    expect(resultSummary("some_weird_tool", "whatever")).toBeNull();
    expect(resultSummary("fill_workbook", "opaque message")).toBeNull();
  });
  test("malformed input (e.g. regex throw) degrades gracefully to null", () => {
    // Defensive: resultSummary() must never throw; returning null is the
    // contract so the caller can safely fall back to the duration badge.
    expect(resultSummary("fill_workbook", "")).toBeNull();
  });
});
