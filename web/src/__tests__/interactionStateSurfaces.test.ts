import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";

const read = (path: string) => readFileSync(path, "utf8");

describe("interactive states use surfaces instead of accent lines", () => {
  test("shared hover, navigation, tab, and selection states contain no accent edges", () => {
    expect(read("src/index.css")).not.toContain("box-shadow: inset 0 -3px 0 #FD5108");
    expect(read("src/index.css")).not.toContain("border-top-color: #FD5108");
    expect(read("src/lib/uiStyles.ts")).not.toContain("borderBottom: `2px solid ${tokens.color.brand.indicator}`");
    expect(read("src/components/HistoryList.tsx")).not.toContain("inset 3px 0 0 0");
    expect(read("src/pages/ConceptsPage.tsx")).not.toContain("inset 0 -2px 0");
    expect(read("src/components/PreRunPanel.tsx")).not.toContain("borderLeft: `4px solid ${pwc.orange500}`");
    expect(read("src/components/ToolCallCard.tsx")).not.toContain("borderLeft: `3px solid ${pwc.orange500}`");
    expect(read("src/components/NotesReviewTab.tsx")).not.toContain("borderLeftColor: expanded ? pwc.orange500");
  });
});
