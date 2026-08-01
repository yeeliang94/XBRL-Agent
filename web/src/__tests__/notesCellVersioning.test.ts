import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { patchNotesCell } from "../lib/notesCells";

/**
 * The optimistic version token, at the layer where it was missing.
 *
 * Peer review found the 409 could never fire in the real editor: the server
 * check was built and tested, but the client never sent a token and the
 * backend explicitly opts out when one is absent. Testing the endpoint alone
 * proved nothing about the feature.
 *
 * The token is `content_revision` — a monotonic counter. `updated_at` has
 * one-second precision, so two saves inside the same second shared it and
 * neither was refused.
 */

const originalFetch = globalThis.fetch;
let calls: { url: string; init?: RequestInit }[];

beforeEach(() => {
  calls = [];
  globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({
        sheet: "Notes", row: 10, label: "L", html: "<p>x</p>",
        evidence: null, source_pages: [], updated_at: "2026-08-01T00:00:00",
        content_revision: 8,
      }),
    } as unknown as Response;
  }) as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function body() {
  return JSON.parse(String(calls[0].init!.body));
}

describe("notes cell versioning", () => {
  test("a save sends the revision it read", async () => {
    await patchNotesCell(7, "Notes", 10, "<p>x</p>", 5);
    expect(body()).toEqual({ html: "<p>x</p>", expected_revision: 5 });
  });

  test("revision 0 is sent, not dropped as falsy", async () => {
    // A freshly-inserted cell can legitimately be at a low revision; treating
    // 0 as "no token" would silently disable the check on exactly those cells.
    await patchNotesCell(7, "Notes", 10, "<p>x</p>", 0);
    expect(body()).toEqual({ html: "<p>x</p>", expected_revision: 0 });
  });

  test("a cell with no revision yet omits the token rather than sending null", async () => {
    await patchNotesCell(7, "Notes", 10, "<p>x</p>", null);
    expect(body()).toEqual({ html: "<p>x</p>" });
    expect("expected_revision" in body()).toBe(false);
  });

  test("omitting the argument entirely still works for other callers", async () => {
    await patchNotesCell(7, "Notes", 10, "<p>x</p>");
    expect(body()).toEqual({ html: "<p>x</p>" });
  });

  test("the response carries the refreshed token for the next save", async () => {
    const updated = await patchNotesCell(7, "Notes", 10, "<p>x</p>", 5);
    expect(updated.content_revision).toBe(8);
  });
});
