import { beforeEach, describe, expect, test } from "vitest";
import { readRunTabFromUrl, replaceRunTabInUrl, writeRunTabToUrl } from "../lib/runTabs";

describe("run section URLs", () => {
  beforeEach(() => window.history.replaceState({}, "", "/"));
  test("replacing a section preserves the current history entry and other URL state", () => {
    const historyState = { xbrlHistoryIndex: 3, selectedRun: 42 };
    window.history.replaceState(historyState, "", "/history/42?tab=notes&view=compare#source");
    const historyLength = window.history.length;
    replaceRunTabInUrl("agents");
    expect(readRunTabFromUrl()).toBe("agents");
    expect(window.location.pathname).toBe("/history/42");
    expect(window.location.search).toBe("?tab=agents&view=compare");
    expect(window.location.hash).toBe("#source");
    expect(window.history.state).toEqual(historyState);
    expect(window.history.length).toBe(historyLength);
  });
  test("the legacy Figures alias lets an explicit section survive reload", () => {
    window.history.replaceState({}, "", "/concepts/42/");
    expect(readRunTabFromUrl()).toBe("values");
    writeRunTabToUrl("notes");
    expect(readRunTabFromUrl()).toBe("notes");
    expect(window.location.pathname).toBe("/concepts/42/");
  });
});
