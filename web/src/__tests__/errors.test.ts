import { describe, test, expect } from "vitest";
import {
  ApiError,
  userMessage,
  technicalDetail,
  extractErrorDetail,
  statusSentence,
} from "../lib/errors";

describe("statusSentence", () => {
  test("maps common status classes to plain, actionable sentences", () => {
    expect(statusSentence(403)).toMatch(/permission/i);
    expect(statusSentence(404)).toMatch(/couldn't find|could not find/i);
    expect(statusSentence(409)).toMatch(/conflict|refresh/i);
    expect(statusSentence(413)).toMatch(/too large/i);
    expect(statusSentence(422)).toMatch(/check|valid/i);
    expect(statusSentence(500)).toMatch(/server|try again/i);
    expect(statusSentence(503)).toMatch(/server|try again/i);
  });
  test("never returns a bare status code", () => {
    for (const s of [400, 401, 403, 404, 409, 413, 422, 429, 500, 502, 503]) {
      expect(statusSentence(s)).not.toMatch(/^\d+$/);
      expect(statusSentence(s)).not.toContain(String(s));
    }
  });
});

describe("extractErrorDetail", () => {
  test("returns a plain string body verbatim", () => {
    expect(extractErrorDetail({ detail: "Pick at least one statement." })).toBe(
      "Pick at least one statement.",
    );
  });
  test("flattens a pydantic error array into readable lines", () => {
    const body = {
      detail: [
        { loc: ["body", "statements"], msg: "Field required", type: "missing" },
        { loc: ["body", "model"], msg: "Input should be a string", type: "string_type" },
      ],
    };
    const out = extractErrorDetail(body)!;
    expect(out).toMatch(/statements/i);
    expect(out).toMatch(/Field required/i);
    // Never the object placeholder.
    expect(out).not.toContain("[object Object]");
  });
  test("handles mtool input_errors object", () => {
    const out = extractErrorDetail({ detail: { input_errors: ["column_map is required"] } })!;
    expect(out).toContain("column_map is required");
    expect(out).not.toContain("[object Object]");
  });
  test("object entries inside errors/input_errors never render [object Object]", () => {
    const out = extractErrorDetail({ detail: { errors: [{ detail: "bad column map" }] } })!;
    expect(out).toContain("bad column map");
    expect(out).not.toContain("[object Object]");
    const out2 = extractErrorDetail({
      detail: { input_errors: [{ loc: ["body", "sheet"], msg: "unknown sheet" }] },
    })!;
    expect(out2).toMatch(/Sheet: unknown sheet/);
    expect(out2).not.toContain("[object Object]");
    // A truly opaque object falls back to compact JSON, still not the placeholder.
    const out3 = extractErrorDetail({ detail: { errors: [{ code: 7 }] } })!;
    expect(out3).not.toContain("[object Object]");
  });
  test("returns null for an unusable body", () => {
    expect(extractErrorDetail({})).toBeNull();
    expect(extractErrorDetail(null)).toBeNull();
    expect(extractErrorDetail("not json")).toBeNull();
  });
});

describe("ApiError", () => {
  test("carries status + technical detail alongside the friendly message", () => {
    const e = new ApiError("Some of the details weren't valid.", {
      status: 422,
      technical: "statements: Field required",
    });
    expect(e).toBeInstanceOf(Error);
    expect(e.name).toBe("ApiError");
    expect(e.status).toBe(422);
    expect(e.technical).toBe("statements: Field required");
    expect(userMessage(e)).toBe("Some of the details weren't valid.");
    expect(technicalDetail(e)).toBe("statements: Field required");
  });
});

describe("userMessage", () => {
  test("passes through a plain Error message", () => {
    expect(userMessage(new Error("Could not reach the server."))).toBe(
      "Could not reach the server.",
    );
  });
  test("passes through a bare string", () => {
    expect(userMessage("Upload failed.")).toBe("Upload failed.");
  });
  test("never surfaces [object Object] for a thrown object", () => {
    expect(userMessage({ weird: true })).not.toContain("[object Object]");
    expect(userMessage({ weird: true })).toMatch(/something went wrong|try again/i);
  });
  test("never surfaces a bare 'HTTP 422' style message", () => {
    // A message that is only an HTTP code is replaced by a real sentence.
    expect(userMessage(new Error("HTTP 422"))).not.toBe("HTTP 422");
    expect(userMessage(new Error("HTTP 422"))).toMatch(/[a-z]{4,}/i);
  });
  test("null / undefined get a generic fallback", () => {
    expect(userMessage(null)).toMatch(/something went wrong|try again/i);
    expect(userMessage(undefined)).toMatch(/something went wrong|try again/i);
  });
});

describe("technicalDetail", () => {
  test("returns the ApiError technical field when present", () => {
    const e = new ApiError("msg", { technical: "raw stack" });
    expect(technicalDetail(e)).toBe("raw stack");
  });
  test("returns null when there is nothing extra to show", () => {
    expect(technicalDetail(new Error("plain"))).toBeNull();
    expect(technicalDetail("string")).toBeNull();
  });
});
