import { describe, test, expect } from "vitest";
import { describePdfSidecar } from "../lib/pdfSidecar";

/**
 * Wording for the ``pdf_sidecar`` SSE event (docs/PLAN-pdf-source-sidecar.md).
 *
 * The operator needs two facts: did the notes agents get a transcript, and if
 * not, why. Every server-side skip reason must map to a sentence, and an
 * unknown reason must still be quoted rather than dropped — a new server code
 * should never turn the notice blank.
 */
describe("describePdfSidecar", () => {
  test("built: reports the page count and the figure-verification caveat", () => {
    const n = describePdfSidecar({ status: "built", pages: 20 });
    expect(n.built).toBe(true);
    expect(n.title).toMatch(/built/i);
    expect(n.message).toMatch(/20 scanned pages/);
    expect(n.message).toMatch(/verify every number/i);
  });

  test("built: states the tokens the pass consumed when the server reports them", () => {
    const n = describePdfSidecar({ status: "built", pages: 20, usage: { in: 56760, out: 13976 } });
    expect(n.message).toMatch(/used 56,760 in \/ 13,976 out tokens/);
    // No usage → no dangling sentence.
    expect(describePdfSidecar({ status: "built", pages: 20 }).message).not.toMatch(/tokens/);
  });

  test("built: singular page wording", () => {
    expect(describePdfSidecar({ status: "built", pages: 1 }).message).toMatch(/1 scanned page were/);
  });

  test.each([
    ["no_notes_inventory", /did not identify which pages/i],
    ["transcription_incomplete", /partial one could be mistaken/i],
    ["no_pages_transcribed", /no page produced/i],
  ])("skipped %s maps to a plain sentence", (reason, re) => {
    const n = describePdfSidecar({ status: "skipped", reason });
    expect(n.built).toBe(false);
    expect(n.title).toMatch(/not available/i);
    expect(n.message).toMatch(re);
    // Every skip says the run still continues.
    expect(n.message).toMatch(/run continues/i);
  });

  test("too_many_pages quotes the requested count and the cap", () => {
    const n = describePdfSidecar({
      status: "skipped", reason: "too_many_pages", pages_requested: 120, page_cap: 80,
    });
    expect(n.message).toMatch(/120 pages/);
    expect(n.message).toMatch(/limit is 80/);
  });

  test("transcription_incomplete lists the failed pages", () => {
    const n = describePdfSidecar({
      status: "skipped", reason: "transcription_incomplete", failed_pages: [12, 15],
    });
    expect(n.message).toMatch(/Pages that failed: 12, 15/);
  });

  test("an unknown reason is quoted, never swallowed", () => {
    const n = describePdfSidecar({ status: "skipped", reason: "error: TimeoutError" });
    expect(n.message).toMatch(/error: TimeoutError/);
  });

  test("a skip with no reason still produces a sentence", () => {
    const n = describePdfSidecar({ status: "skipped" });
    expect(n.message).toMatch(/did not complete/i);
  });
});
