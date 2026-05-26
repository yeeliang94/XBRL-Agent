// Parse the PDF page numbers cited in an extracted value's `evidence` string.
//
// Evidence is free-text the agent wrote (e.g. "Page 14, Note 1"), so this is
// deliberately forgiving. It recognises the accountant-style notations the
// agents actually emit:
//
//   "Page 14, Note 1"        -> [14]
//   "Pages 19-20, Note 2(g)" -> [19, 20]   (range expanded)
//   "Page 3; Page 4"         -> [3, 4]
//   "p.42"                   -> [42]
//
// Result is deduped and ascending. Unparseable / null input -> []. The PDF
// pane uses an empty result to show "no source page recorded" rather than
// breaking — bad evidence becomes visible, not fatal.

// One matcher per page reference: "page"/"pages"/"p" + optional dot/space,
// then a number, then optionally a "-N" range tail. The global flag lets a
// single string carry several refs ("Page 3; Page 4").
const PAGE_REF = /\bp(?:age)?s?\.?\s*(\d+)\s*(?:[-–]\s*(\d+))?/gi;

// A defensive ceiling on range expansion. Real statements never cite a
// thousand-page span; a malformed "Pages 1-99999" must not balloon the array.
const MAX_RANGE_SPAN = 200;

export function parseEvidencePages(evidence: string | null | undefined): number[] {
  if (!evidence) return [];

  const found = new Set<number>();
  for (const match of evidence.matchAll(PAGE_REF)) {
    const start = Number(match[1]);
    if (!Number.isFinite(start) || start < 1) continue;

    const end = match[2] != null ? Number(match[2]) : start;
    // A backwards or implausibly wide range collapses to the single start
    // page rather than producing garbage.
    if (!Number.isFinite(end) || end < start || end - start > MAX_RANGE_SPAN) {
      found.add(start);
      continue;
    }
    for (let p = start; p <= end; p++) found.add(p);
  }

  return [...found].sort((a, b) => a - b);
}
