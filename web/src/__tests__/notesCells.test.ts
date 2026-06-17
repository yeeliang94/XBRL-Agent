import { describe, test, expect } from "vitest";
import {
  sortSheetsBySlot,
  parseNumericInput,
  INVALID_NUMBER,
  type NotesSheet,
} from "../lib/notesCells";

// ---------------------------------------------------------------------------
// sortSheetsBySlot — orders sheets by MBRS slot index (Corp Info → Acc
// Policies → List of Notes → Issued Capital → Related Party) rather than
// the alphabetical order SQLite emits. The DB query
// (`list_notes_cells_for_run` in db/repository.py) orders by (sheet, row)
// which puts `Notes-Listofnotes` before `Notes-SummaryofAccPol` — exactly
// the bug that surfaced in the History-tab review modal.
//
// Sheet-name parity: notes_types.py:71 states the sheet names are identical
// across MFRS and MPERS, so one ordering array is enough for both filing
// standards.
// ---------------------------------------------------------------------------

function sheet(name: string): NotesSheet {
  return { sheet: name, rows: [] };
}

describe("sortSheetsBySlot", () => {
  test("returns known sheets in MBRS slot order even when input is alphabetical", () => {
    // Input order mimics the SQLite ORDER BY sheet — pure ASCII sort.
    const input = [
      sheet("Notes-CI"),
      sheet("Notes-Issuedcapital"),
      sheet("Notes-Listofnotes"),
      sheet("Notes-RelatedPartytran"),
      sheet("Notes-SummaryofAccPol"),
    ];
    const out = sortSheetsBySlot(input).map((s) => s.sheet);
    expect(out).toEqual([
      "Notes-CI",              // slot 10 (MFRS) / 11 (MPERS)
      "Notes-SummaryofAccPol", // slot 11 / 12
      "Notes-Listofnotes",     // slot 12 / 13
      "Notes-Issuedcapital",   // slot 13 / 14
      "Notes-RelatedPartytran",// slot 14 / 15
    ]);
  });

  test("unknown sheet names sort to the end, alphabetised among themselves", () => {
    // Defensive path: if notes_types.py ever adds a new template, the
    // frontend shouldn't drop it — put unknowns at the tail so reviewers
    // still see the content.
    const input = [
      sheet("Notes-Zeta"),
      sheet("Notes-CI"),
      sheet("Notes-Alpha"),
    ];
    const out = sortSheetsBySlot(input).map((s) => s.sheet);
    expect(out).toEqual(["Notes-CI", "Notes-Alpha", "Notes-Zeta"]);
  });

  test("does not mutate the input array", () => {
    const input = [sheet("Notes-Listofnotes"), sheet("Notes-CI")];
    const snapshot = input.map((s) => s.sheet);
    sortSheetsBySlot(input);
    expect(input.map((s) => s.sheet)).toEqual(snapshot);
  });
});

// ---------------------------------------------------------------------------
// parseNumericInput — accountant-formatted number entry for numeric notes.
// Reviewers paste straight from financial statements (thousands separators,
// parenthesised negatives), so those must parse instead of failing as NaN.
// ---------------------------------------------------------------------------

describe("parseNumericInput", () => {
  test("empty / whitespace-only is null (clears the cell)", () => {
    expect(parseNumericInput("")).toBeNull();
    expect(parseNumericInput("   ")).toBeNull();
  });

  test("plain numbers parse, including a typed zero", () => {
    expect(parseNumericInput("1234")).toBe(1234);
    expect(parseNumericInput("1234.5")).toBe(1234.5);
    expect(parseNumericInput("0")).toBe(0);
    expect(parseNumericInput("-95")).toBe(-95);
  });

  test("strips thousands separators and surrounding whitespace", () => {
    expect(parseNumericInput(" 1,234,567 ")).toBe(1234567);
    expect(parseNumericInput("1,000.50")).toBe(1000.5);
  });

  test("reads parenthesised values as negatives", () => {
    expect(parseNumericInput("(95)")).toBe(-95);
    expect(parseNumericInput("(1,234.5)")).toBe(-1234.5);
  });

  test("non-numeric text returns the INVALID_NUMBER sentinel", () => {
    expect(parseNumericInput("abc")).toBe(INVALID_NUMBER);
    expect(parseNumericInput("(")).toBe(INVALID_NUMBER);
    expect(parseNumericInput("1.2.3")).toBe(INVALID_NUMBER);
    // A lone dash isn't a number — caller surfaces an error rather than 0.
    expect(parseNumericInput("-")).toBe(INVALID_NUMBER);
  });
});
