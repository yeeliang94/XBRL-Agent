import { describe, test, expect } from "vitest";
import { sortSheetsBySlot, type NotesSheet } from "../lib/notesCells";

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
