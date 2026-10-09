import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { pwc, tokens, component } from "../lib/theme";
import { ui } from "../lib/uiStyles";

function readReference(relativePath: string): string {
  const url = new URL(relativePath, import.meta.url);
  let path = decodeURIComponent(url.pathname).replace(/^\/@fs\//, "/");
  if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1);
  return readFileSync(path, "utf8");
}

const designSystem = readReference("../../../docs/xbrl-design-system.html");
const prototype = readReference("../../../docs/prototype-ui-overhaul.html");

describe("XBRL design system is the production authority", () => {
  test("keeps guidance contextual and long prompt sources behind a disclosure", () => {
    expect(designSystem).toContain("Place shared team guidance in Settings, immediately after General");
    expect(designSystem).toContain("View application instructions");
    expect(designSystem).toContain("Team guidance used");
    expect(designSystem).toContain("Preserve unsaved text on save failure or concurrent-edit conflict");
    expect(designSystem).toContain("browser Back and Forward");
    expect(designSystem).toContain("including after native fragment navigation, and before logout");
    expect(designSystem).toContain("skip link preserves the editor without a discard prompt");
  });
  test("groups shared settings and requires a scoped reset confirmation", () => {
    expect(designSystem).toContain("Prior-run hints are disabled by the application defaults");
    expect(designSystem).toContain("automatic review, tolerance, source-integrity information and optional feature switches in Advanced");
    expect(designSystem).toContain("Unrelated saves preserve configured prior-run hints");
    expect(designSystem).toContain("Prepared documents with notes always enforce source-integrity checks");
    expect(designSystem).toContain("Preserve the legacy source-check mode and name it in the reset confirmation");
    expect(designSystem).toContain("Notes appearance keeps Custom colour selected when its colour matches a preset");
    expect(designSystem).toContain("Disable reset confirmation while an appearance save is in progress");
    expect(designSystem).toContain("saved workstream tabs name and control their selected panel through matching ARIA references");
    expect(designSystem).toContain("Keep unsaved shared edits");
    expect(designSystem).toContain("require confirmation that names the shared scope");
    expect(designSystem).toContain("preserve the connection, access key, team guidance, accounts and existing runs");
  });
  test("keeps saved activity completion and sub-agent identity truthful", () => {
    expect(designSystem).toContain("Saved Activity uses recorded parent completion events when agent rows still report running");
    expect(designSystem).toContain("final database failures and cancellations remain authoritative");
    expect(designSystem).toContain("Recorded List of Notes sub-agent identities stay selectable");
    expect(designSystem).toContain("Never invent missing ranges");
  });
  test("declares the canonical production contract and Direction A", () => {
    expect(designSystem).toContain("CANONICAL PRODUCTION DESIGN SYSTEM");
    expect(designSystem).toContain("STATUS · PRODUCTION");
    expect(designSystem).toContain("DIRECTION · A");
    expect(prototype).toContain("Direction A — focused workspace");
  });

  test("keeps notes field hierarchy and coverage in the source inventory", () => {
    expect(designSystem).toContain("plain bold field headings above white note content");
    expect(designSystem).toContain("Selected Notes items use the shared Grey 100 selected surface and medium weight");
    expect(designSystem).toContain("Comparison pairs share one field heading and shared grid rows for actions, editing tools and content");
    expect(designSystem).toContain("Open prose fields that hold AI or human content by default as bordered read-only previews");
    expect(designSystem).toContain("keep empty fields collapsed in compact clickable rows");
    expect(designSystem).toContain("Give human note blocks the same thin neutral border");
    expect(designSystem).toContain("do not repeat the checklist below the editor");
    expect(designSystem).toContain("without warning counts or repeated review banners");
    expect(designSystem).toContain("Keep incomplete output, failed saves, and actionable filing issues visible");
    expect(designSystem).toContain("Source inventory remains visible in the worksheet rail");
    expect(designSystem).toContain("A completed run must not turn failed or stopped formatting into Complete");
    expect(designSystem).toContain("Content finalization follows notes review and precedes source completeness checking and final formatting");
    expect(designSystem).toContain("Later content, source or prepared-output changes invalidate appearance verification");
  });

  test.each([
    ["orange700", "--orange-700"],
    ["orange500", "--orange-500"],
    ["orange400", "--orange-400"],
    ["orange300", "--orange-300"],
    ["orange200", "--orange-200"],
    ["orange100", "--orange-100"],
    ["orange50", "--orange-50"],
    ["grey500", "--grey-500"],
    ["grey400", "--grey-400"],
    ["grey300", "--grey-300"],
    ["grey200", "--grey-200"],
    ["grey100", "--grey-100"],
    ["grey50", "--grey-50"],
  ] as const)("pins %s to the canonical reference", (token, cssName) => {
    const declaration = `${cssName}: ${pwc[token].toLowerCase()}`;
    expect(designSystem.toLowerCase()).toContain(declaration);
    expect(prototype.toLowerCase()).toContain(declaration);
  });

  test("pins black and white to the prototype's ink and canvas aliases", () => {
    expect(designSystem.toLowerCase()).toContain(`--black: ${pwc.black.toLowerCase()}`);
    expect(designSystem.toLowerCase()).toContain(`--white: ${pwc.white.toLowerCase()}`);
    expect(prototype.toLowerCase()).toContain(`--ink: ${pwc.black.toLowerCase()}`);
    expect(prototype.toLowerCase()).toContain(`--canvas: ${pwc.white.toLowerCase()}`);
  });

  test("pins typography, spacing, radius, and flat-surface rules", () => {
    expect(pwc.fontBody).toBe('"Inter Variable", Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif');
    expect(pwc.fontMono).toBe('"SFMono-Regular", Consolas, "Liberation Mono", monospace');
    expect(pwc.radius).toMatchObject({ sm: 6, md: 8, lg: 12 });
    expect(pwc.space).toEqual({ xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48, xxxxl: 64 });
    expect(pwc.shadow.card).toBe("none");
    expect(pwc.shadow.elevated).toBe("0 14px 40px rgba(0, 0, 0, 0.12)");
    expect(ui.card.boxShadow).toBeUndefined();
    expect(tokens.space.pageGutter).toBe(32);
    expect(tokens.space.paneInset).toBe(24);
    expect(tokens.space.paragraph).toBe(12);
    expect(ui.readingText).toMatchObject({ fontSize: 14, lineHeight: 1.6, maxWidth: "70ch" });
    expect(designSystem).toContain("Spacing and reading rhythm");
    expect(designSystem).toContain("Forms, tables and dialogs");
    expect(designSystem).toContain("Checked uses black fill with a white tick");
    expect(designSystem).toContain("partially selected uses a white dash");
    expect(designSystem).toContain("Selected options use bold black text and a small right-pointing chevron without a fill");
    expect(designSystem).toContain("other browsers and touch devices retain the native picker");
    expect(designSystem).toContain("aligned current-year and previous-year columns");
    expect(designSystem).toContain("Show saved summaries without a year label when period detail is unavailable");
    expect(designSystem).not.toContain('aria-label="More actions"');
  });
});

describe("Direction A semantic roles", () => {
  test("black carries actions while orange stays activity/attention", () => {
    expect(tokens.color.action.primary).toBe(pwc.black);
    expect(tokens.color.action.primaryHover).toBe(pwc.black);
    expect(component.button.primary.background).toBe(pwc.black);
    expect(tokens.color.brand.accent).toBe(pwc.orange500);
    expect(prototype).toContain(".shell-a .button.primary");
    expect(designSystem).toContain("Orange identifies activity and attention");
  });

  test("text and control roles match the exact XBRL values", () => {
    expect(tokens.color.text.primary).toBe(pwc.black);
    expect(tokens.color.text.body).toBe(pwc.black);
    expect(tokens.color.text.secondary).toBe("rgba(0, 0, 0, 0.64)");
    expect(tokens.color.text.muted).toBe("rgba(0, 0, 0, 0.46)");
    expect(tokens.color.border.control).toBe(pwc.grey500);
  });

  test("uses the three-level type scale and exact action geometry", () => {
    expect(ui.pageTitle.fontSize).toBe(28);
    expect(ui.sectionTitle.fontSize).toBe(16);
    expect(ui.bodyText.fontSize).toBe(14);
    expect(designSystem).toContain(`--type-page: ${ui.pageTitle.fontSize}px`);
    expect(designSystem).toContain(`--type-section: ${ui.sectionTitle.fontSize}px`);
    expect(designSystem).toContain(`--type-body: ${ui.bodyText.fontSize}px`);
    expect(designSystem).toContain(`--type-metadata: ${ui.metadata.fontSize}px`);
    expect(designSystem).toContain(`--weight-regular: ${pwc.weight.regular}`);
    expect(designSystem).toContain(`--weight-control: ${pwc.weight.medium}`);
    expect(designSystem).toContain(`--weight-emphasis: ${pwc.weight.semibold}`);
    expect(ui.buttonPrimary.minHeight).toBe(40);
    expect(ui.buttonPrimary.padding).toBe("0 15px");
    expect(ui.buttonSm.minHeight).toBe(40);
  });
});

describe("Direction A shell and responsive composition", () => {
  test("pins the 220px rail, 64px top bar, 1500px canvas, and manual 72px rail", () => {
    expect(ui.appShell.gridTemplateColumns).toBe("220px minmax(0, 1fr)");
    expect(ui.appTopbar.height).toBe(64);
    expect(tokens.layout.standard).toBe(1500);
    expect(tokens.layout.wideList).toBe(1500);
    expect(prototype).toContain(".shell-a { display: grid; grid-template-columns: 220px minmax(0,1fr)");
    expect(prototype).toContain(".a-topbar { height: 64px");
    expect(prototype).toContain("max-width: 1500px");
    expect(prototype).toContain(".shell-a.is-collapsed { grid-template-columns: 72px minmax(0,1fr)");
    expect(prototype).toContain('data-rail-toggle');
    expect(prototype).not.toContain('["review","notes"].includes(page) ? "review-mode"');
  });

  test("documents one persistent run navigation and explicit completion", () => {
    expect(designSystem).toContain("the retired pre-extraction transcript has no live tab");
    expect(designSystem).toContain("Historical transcript notices remain available in run Overview");
    expect(designSystem).toContain("Work queue is the home workspace");
    expect(designSystem).toContain("Remember the collapse choice across refreshes");
    expect(designSystem).toContain("Collapsing navigation must not remount the current workspace");
    expect(designSystem).toContain("Missing summary data displays a dash");
    expect(designSystem).toContain("Of these, not started identifies the draft subset of that same queue");
    expect(designSystem).toContain("without working glows or pulsing indicators");
    expect(designSystem).toContain("including Confirm setup");
    expect(designSystem).toContain("preserves the document and selected section through refresh");
    expect(designSystem).toContain("block leaving Settings or reloading while an appearance save is pending or saving");
    expect(designSystem).toContain("prepared documents also rerun Corporate Information, Accounting Policies and List of Notes and replace edits in every retried template");
    expect(designSystem).toContain("Browser Back and Forward retrace visited sections");
    expect(designSystem).toContain("Completion does not mean human review is finished");
    expect(designSystem).toContain("When tabs change, bring the new panel into view");
    expect(prototype).toContain('aria-label="Run detail sections"');
    expect(prototype).toContain('page,"Current run","◉"');
    expect(prototype).not.toContain('page,"Figures review","⌗"');
  });

  test("documents desktop, tablet, mobile, keyboard, and reduced-motion behavior", () => {
    for (const requirement of [
      "Large desktop:",
      "Standard desktop:",
      "Tablet:",
      "Mobile:",
      "Every icon-only control",
      "prefers-reduced-motion",
    ]) {
      expect(designSystem).toContain(requirement);
    }
    expect(prototype).toContain("@media (max-width: 1100px)");
    expect(prototype).toContain("@media (max-width: 780px)");
  });

  test("defines restrained motion for controls, content, dialogs, and status", () => {
    for (const requirement of [
      "Motion and feedback",
      "Animate a loaded list or table once as a region, not every row",
      "Reserve repeating motion for an active wait",
      "never delay focus, navigation or a result",
    ]) {
      expect(designSystem).toContain(requirement);
    }
  });

  test("pins one icon family and its colour roles", () => {
    for (const requirement of [
      "Google Material Symbols, Rounded, weight 300, unfilled",
      "never type a Unicode symbol",
      "Current place and live work",
      "Routine success stays monochrome",
      "Destructive actions use an orange outline",
    ]) {
      expect(designSystem).toContain(requirement);
    }
  });

  test("forbids line-based hover and selected-state indicators", () => {
    expect(designSystem).toContain("Do not use accent lines for hover, pressed, active or selected states");
    expect(prototype).toContain("Interactive states use surface, text and icon changes; never accent edge or underline indicators");
    expect(designSystem).not.toContain("box-shadow: inset 0 -3px 0 var(--orange-500)");
    expect(prototype).not.toContain("box-shadow: inset 0 -3px 0 var(--accent)");
  });

  test("pins the worksheet-oriented Notes review composition", () => {
    expect(designSystem).toContain("mTool worksheet navigation with the source note inventory");
    expect(designSystem).toContain("one-pixel visible Grey 100 rule");
    expect(designSystem).toContain("hide it only when those panes stack");
    expect(designSystem).toContain("mount one rich-text editor for the selected field");
    expect(designSystem).toContain("PDF controls remain in one sticky toolbar");
    expect(prototype).toContain(".notes-three-pane { display: grid; grid-template-columns: 240px 9px minmax(410px, 1fr) 9px minmax(330px, 35%)");
    expect(prototype).toContain('aria-label="Notes sheet navigator"');
    expect(prototype).toContain('aria-label="Resize source notes"');
    expect(prototype).toContain("Sources (3)");
  });

  test("pins the concise operator activity stream contract", () => {
    expect(designSystem).toContain("Opening a running document defaults to Activity and exposes only Overview and Activity");
    expect(designSystem).toContain("Restore the review tabs after completion, failure or stopping");
    expect(designSystem).toContain("Activity provides an Export diagnostics button for the selected run");
    expect(designSystem).toContain("Running and saved runs share one grouped navigator and selected activity pane");
    expect(designSystem).toContain("even when no agents started or no output folder exists");
    expect(designSystem).toContain("one flat chronological <em>Live activity</em> stream (or <em>Recorded activity</em> for settled workstreams)");
    expect(designSystem).toContain("no event cards, navigation buttons or nested activity panels");
    expect(designSystem).toContain("Follow new updates only while the operator remains at the bottom");
    expect(designSystem).toContain("Keep tool operations, provider reasoning, tokens and request metadata in closed technical diagnostics");
    expect(designSystem).toContain("the activity roster shows item states without a second completion total");
    expect(prototype).toContain("Live activity");
    expect(prototype).not.toContain("Provider reasoning");
    expect(prototype).not.toContain("1 / 8");
  });
});


describe("Simplified template-oriented review", () => {
  test("pins the minimal Notes review contract", () => {
    expect(designSystem).toContain("Notes formatting rechecks use one compact notice beside the action, without internal row numbers");
    expect(designSystem).toContain("Notes use one divider between peer fields");
    for (const requirement of [
      "Use the field heading as the disclosure control for populated and empty fields",
      "Offer a keyboard-accessible Open editor action beside each populated field heading",
      "Offer Hide empty fields for the current worksheet",
      "Preserve level-three field headings and announce filing warnings and comparison markers as descriptions of the disclosure button",
      "Derive prose comparison markers and the Missed by AI filter from current extracted and human content",
      "A comparison view offers an Only missed by AI checkbox",
      "Keep the active field open when changing views",
      "Include human-only numeric categories only for the worksheet’s applicable periods and entity scopes",
      "hide these headings when no pair is open",
      "Do not show evidence prose, appearance provenance, internal row numbers",
      "Appearance defaults belong in Settings",
      "meaningful destination subheadings, never technical sheet names",
      "immediately returns the PDF to the source note's cited page",
      "description columns receive priority and wrap",
      "Filled note previews and editors expand to their full content height without nested vertical scrolling",
      "Human comparison notes use the same review typography, paragraph spacing and table padding as extracted notes",
      "Align mTool worksheets, Note content and Source PDF in one shared 44px heading band",
    ]) {
      expect(designSystem).toContain(requirement);
    }
    expect(designSystem).not.toContain("Re-extract notes");
  });

  test("pins the application-wide clarity contract", () => {
    for (const requirement of [
      "where they are, what needs attention and the next available action",
      "Show outcomes, not implementation details",
      "Reserve indicators for exceptions",
      "A completed workstream count must not imply the whole run is ready",
      "Reselecting the same item returns to that page even after manual paging",
    ]) {
      expect(designSystem).toContain(requirement);
    }
  });

  test("pins consistent run actions and human-file comparison placement", () => {
    expect(designSystem).toContain("Place Redo and Delete together at the end of Overview under Run actions");
    expect(designSystem).toContain("A human-file comparison header spans the full review workspace");
    expect(designSystem).toContain("label the AI and human note columns once above the open fields");
    expect(designSystem).toContain("The comparison header is one line");
    expect(designSystem).toContain("Human values are read-only text without a field border");
    expect(designSystem).toContain("A select is the same flat white field with one thin chevron");
    expect(designSystem).toContain("align each pane title with its own content edge");
    expect(designSystem).toContain("Keep the worksheet rails visible at standard desktop widths");
    expect(designSystem).toContain("Name a single review issue instead of showing only its count");
  });

  test("pins the concise mTool preparation workflow", () => {
    for (const requirement of [
      "upload template, fill safe matches, download",
      "Put sheet and note options in one closed Customize disclosure",
      "meaningful statement names rather than technical worksheet keys",
      "Missing destinations are skipped, not guessed",
      "Show unknown settings as unverified",
      "Name the entry action Prepare mTool draft",
    ]) {
      expect(designSystem).toContain(requirement);
    }
  });
});


test("Figures keep routine provenance in Field details without implying verification", () => {
  for (const reference of [designSystem, prototype]) {
    expect(reference).toContain("Routine value-origin labels appear only in Field details");
    expect(reference).toContain("Recorded values never imply verification");
  }
});


test("Figures distinguish editable inputs from plain read-only values", () => {
  expect(designSystem).toContain("Editable figure values always retain a Grey 300 input border");
  expect(designSystem).toContain("Calculated and linked values are plain read-only numbers with no border or field fill");
  expect(designSystem).toContain("Empty read-only fields display a 12px em dash");
  // JSDOM does not apply global CSS; pin against the rule that hid input borders.
  const css = readReference("../index.css");
  expect(css).not.toContain('.concept-tree-row[aria-selected="false"] input:not(:focus)');
});
