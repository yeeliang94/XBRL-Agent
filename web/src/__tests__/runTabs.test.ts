import { beforeEach, describe, expect, test } from "vitest";
import { readRunTabFromUrl, writeRunTabToUrl } from "../lib/runTabs";

describe("run section URLs", () => {
  beforeEach(() => window.history.replaceState({}, "", "/"));
  test("the legacy Figures alias lets an explicit section survive reload", () => {
    window.history.replaceState({}, "", "/concepts/42/");
    expect(readRunTabFromUrl()).toBe("values");
    writeRunTabToUrl("notes");
    expect(readRunTabFromUrl()).toBe("notes");
    expect(window.location.pathname).toBe("/concepts/42/");
  });
});
