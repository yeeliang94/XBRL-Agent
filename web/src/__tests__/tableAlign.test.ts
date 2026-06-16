import { describe, test, expect } from "vitest";
import {
  isNumericCellText,
  shouldRightAlignCell,
  tagNumericCells,
} from "../lib/tableAlign";

describe("isNumericCellText", () => {
  test("matches accountant-style numbers", () => {
    expect(isNumericCellText("1,595")).toBe(true);
    expect(isNumericCellText("(95)")).toBe(true);
    expect(isNumericCellText("17,925")).toBe(true);
    expect(isNumericCellText("1.5")).toBe(true);
    expect(isNumericCellText("-95")).toBe(true);
    expect(isNumericCellText("—")).toBe(true);
    expect(isNumericCellText("-")).toBe(true);
    expect(isNumericCellText("  1,000  ")).toBe(true);
  });

  test("rejects text and mixed cells", () => {
    expect(isNumericCellText("Revenue")).toBe(false);
    expect(isNumericCellText("2024 RM'000")).toBe(false);
    expect(isNumericCellText("")).toBe(false);
  });
});

describe("shouldRightAlignCell", () => {
  test("first column of a multi-column row stays left even if numeric", () => {
    expect(shouldRightAlignCell("1,000", 0, 3)).toBe(false);
    expect(shouldRightAlignCell("Approved", 0, 3)).toBe(false);
  });

  test("numeric value columns right-align", () => {
    expect(shouldRightAlignCell("1,595", 1, 3)).toBe(true);
    expect(shouldRightAlignCell("(95)", 2, 3)).toBe(true);
  });

  test("non-numeric value columns stay left", () => {
    expect(shouldRightAlignCell("2024 RM'000", 1, 3)).toBe(false);
  });

  test("bare single-cell numeric row still right-aligns", () => {
    expect(shouldRightAlignCell("1,000", 0, 1)).toBe(true);
  });
});

describe("tagNumericCells", () => {
  test("tags numeric value cells and exempts the label column", () => {
    const root = document.createElement("div");
    root.innerHTML =
      "<table>" +
      "<tr><th>Item</th><th>2024</th><th>2023</th></tr>" +
      "<tr><td>Approved and contracted for</td><td>1,595</td><td>265</td></tr>" +
      "<tr><td>Total</td><td>17,925</td><td>20,094</td></tr>" +
      "</table>";

    tagNumericCells(root);

    const rows = Array.from(root.querySelectorAll("tr"));
    // Header: bare "2024"/"2023" are numeric and in value columns → right.
    const header = Array.from(rows[0].children) as HTMLElement[];
    expect(header[0].classList.contains("is-numeric")).toBe(false); // Item
    expect(header[1].classList.contains("is-numeric")).toBe(true); // 2024
    // Data row: label left, numbers right.
    const data = Array.from(rows[1].children) as HTMLElement[];
    expect(data[0].classList.contains("is-numeric")).toBe(false);
    expect(data[1].classList.contains("is-numeric")).toBe(true);
    expect(data[2].classList.contains("is-numeric")).toBe(true);
  });

  test("is idempotent — re-running clears stale tags", () => {
    const root = document.createElement("div");
    root.innerHTML = "<table><tr><td>x</td><td>1</td></tr></table>";
    tagNumericCells(root);
    const cells = Array.from(root.querySelectorAll("td")) as HTMLElement[];
    expect(cells[1].classList.contains("is-numeric")).toBe(true);
    // Mutate the numeric cell to text; re-tag should remove the class.
    cells[1].textContent = "now text";
    tagNumericCells(root);
    expect(cells[1].classList.contains("is-numeric")).toBe(false);
  });
});
