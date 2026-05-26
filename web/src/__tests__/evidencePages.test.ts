import { describe, it, expect } from "vitest";
import { parseEvidencePages } from "../lib/evidencePages";

describe("parseEvidencePages", () => {
  it("parses a single 'Page N' reference", () => {
    expect(parseEvidencePages("Page 14, Note 1")).toEqual([14]);
  });

  it("expands a 'Pages N-M' range", () => {
    expect(parseEvidencePages("Pages 19-20, Note 2(g)")).toEqual([19, 20]);
  });

  it("collects multiple semicolon-separated refs", () => {
    expect(parseEvidencePages("Page 3; Page 4")).toEqual([3, 4]);
  });

  it("handles the 'p.N' shorthand", () => {
    expect(parseEvidencePages("p.42")).toEqual([42]);
  });

  it("returns [] for null/empty/no-page input", () => {
    expect(parseEvidencePages(null)).toEqual([]);
    expect(parseEvidencePages("")).toEqual([]);
    expect(parseEvidencePages("Note 2.7, no page given")).toEqual([]);
  });

  it("dedupes and sorts ascending", () => {
    expect(parseEvidencePages("Page 4; Page 3; Page 4")).toEqual([3, 4]);
  });

  it("collapses an implausibly wide range to its start page", () => {
    expect(parseEvidencePages("Pages 1-99999")).toEqual([1]);
  });

  it("collapses a backwards range to its start page", () => {
    expect(parseEvidencePages("Pages 20-19")).toEqual([20]);
  });
});
