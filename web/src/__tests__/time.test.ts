import { describe, test, expect } from "vitest";
import { formatMMSS, formatElapsedMs } from "../lib/time";

describe("formatMMSS", () => {
  test("0 seconds renders 00:00", () => {
    expect(formatMMSS(0)).toBe("00:00");
  });

  test("65 seconds renders 01:05", () => {
    expect(formatMMSS(65)).toBe("01:05");
  });

  test("null renders em-dash placeholder", () => {
    expect(formatMMSS(null)).toBe("—");
  });

  test("pads minutes and seconds to two digits", () => {
    expect(formatMMSS(3)).toBe("00:03");
    expect(formatMMSS(599)).toBe("09:59");
  });

  test("clamps negative values to 00:00", () => {
    expect(formatMMSS(-5)).toBe("00:00");
  });
});

describe("formatElapsedMs", () => {
  test("1500 ms renders 00:01", () => {
    expect(formatElapsedMs(1500)).toBe("00:01");
  });

  test("65_000 ms renders 01:05", () => {
    expect(formatElapsedMs(65_000)).toBe("01:05");
  });
});
