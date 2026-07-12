import type { CSSProperties } from "react";
import { pwc, tokens, component } from "./theme";

// Shared inline component primitives, modelled on docs/pwc-design-system.html.
// The app intentionally avoids Tailwind / className styling for Windows
// compatibility (CLAUDE.md gotcha #7), so the common UI language lives here
// instead of being recreated per page.
//
// Primitives consume the SEMANTIC token layer (`tokens` / `component` in
// lib/theme.ts) — meaning, not palette names — so pages that spread these
// styles inherit accessibility and identity decisions automatically.
//
// Hover / focus states can't be expressed inline. Components opt into the
// matching global rules in index.css by also setting `className` to one of
// the `uiClass` constants below (e.g. `<button className={uiClass.btnPrimary}
// style={ui.buttonPrimary}>`). Form-control focus rings are applied globally
// to all inputs/selects/textareas in index.css and need no className.

// Control boundaries are essential (WCAG 3:1): grey500, not the decorative
// grey300 (design-system role map: "control border = grey500 · grey300
// remains decorative or disabled only").
const controlBase: CSSProperties = {
  fontFamily: pwc.fontBody,
  fontSize: 15,
  lineHeight: 1.45,
  borderRadius: tokens.radius.control,
  border: `1px solid ${tokens.color.border.control}`,
  background: tokens.surface.default,
  color: tokens.color.text.primary,
};

// Shared button geometry. Variants differ only in colour. Default target is
// 44px; compact desktop controls may use ui.buttonSm (40px) when separated.
const buttonBase: CSSProperties = {
  minHeight: 44,
  padding: "10px 20px",
  fontFamily: pwc.fontHeading,
  fontSize: 15,
  fontWeight: pwc.weight.medium,
  borderRadius: tokens.radius.control,
  border: "1px solid transparent",
  cursor: "pointer",
  textDecoration: "none",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: pwc.space.sm,
  whiteSpace: "nowrap",
  lineHeight: 1.2,
  transition: "background .15s ease, border-color .15s ease",
};

// Outline pill (design-system Badges): transparent fill, a thin status-coloured
// border + matching dot, neutral grey label. RESERVED for exceptional compact
// state identification — routine status uses ui.status (monochrome symbol +
// text). Variants override only borderColor; pair with `ui.badgeDot(<hue>)`.
const badgeBase: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 7,
  minHeight: 22,
  padding: "3px 11px",
  borderRadius: tokens.radius.pill,
  fontFamily: pwc.fontHeading,
  fontSize: 12,
  fontWeight: pwc.weight.medium,
  lineHeight: 1.4,
  whiteSpace: "nowrap",
  background: "transparent",
  color: pwc.grey800,
  border: `1px solid ${pwc.grey300}`,
};

// Restrained alert (design-system Alerts): neutral surface, hairline border,
// a status-coloured left rule + coloured icon carry the state. No coloured
// fills. Variants set the left-rule colour; pair the icon with
// `ui.alertIcon(<hue>)`.
const alertBase: CSSProperties = {
  display: "flex",
  gap: pwc.space.md,
  alignItems: "flex-start",
  padding: pwc.space.lg,
  borderRadius: tokens.radius.control,
  background: tokens.surface.default,
  border: `1px solid ${tokens.color.border.subtle}`,
  borderLeft: `3px solid ${tokens.color.border.strong}`,
  color: tokens.color.text.body,
  fontFamily: pwc.fontBody,
  fontSize: 15,
  lineHeight: 1.55,
};

// Quiet — low-priority toolbar/navigation action. The former Subtle and
// Ghost variants converged into this single role (design-system Buttons).
const buttonQuiet: CSSProperties = {
  ...buttonBase,
  color: pwc.grey800,
  background: "transparent",
};

// The four table-header/cell densities share everything except padding.
// Sentence-case headers (design-system Tables): no tracked uppercase.
const thBase: CSSProperties = {
  textAlign: "left",
  background: component.table.header.surface,
  fontSize: 13,
  letterSpacing: 0,
  color: component.table.header.text,
  fontWeight: pwc.weight.semibold,
  borderBottom: `1px solid ${tokens.color.border.subtle}`,
};

export const ui = {
  // --- Typography --------------------------------------------------------
  // Compact semantic scale (design-system Typography): 28 Page title ·
  // 22 Workspace title · 20 Section · 15–16 Subsection · 15 Body ·
  // 13–14 Data/support · 12–13 Metadata · 11–12 Micro-label. Small text uses
  // grey700 or darker; grey500 is decorative/disabled-adjacent only.
  pageTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 28,
    lineHeight: 1.2,
    fontWeight: pwc.weight.semibold,
    color: tokens.color.text.primary,
    margin: 0,
  } as CSSProperties,
  pageTitleCompact: {
    fontFamily: pwc.fontHeading,
    fontSize: 22,
    lineHeight: 1.2,
    fontWeight: pwc.weight.semibold,
    color: tokens.color.text.primary,
    margin: 0,
  } as CSSProperties,
  sectionTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 20,
    lineHeight: 1.25,
    fontWeight: pwc.weight.semibold,
    color: tokens.color.text.primary,
    margin: 0,
  } as CSSProperties,
  subsectionTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    lineHeight: 1.35,
    fontWeight: pwc.weight.semibold,
    color: tokens.color.text.primary,
    margin: 0,
  } as CSSProperties,
  bodyText: {
    fontFamily: pwc.fontBody,
    fontSize: 15,
    lineHeight: 1.55,
    fontWeight: pwc.weight.regular,
    color: tokens.color.text.body,
  } as CSSProperties,
  supportingText: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    lineHeight: 1.5,
    fontWeight: pwc.weight.regular,
    color: tokens.color.text.secondary,
  } as CSSProperties,
  metadata: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    lineHeight: 1.45,
    fontWeight: pwc.weight.regular,
    color: tokens.color.text.secondary,
  } as CSSProperties,
  // Micro-label: short eyebrows and compact keys ONLY — never table headers,
  // long labels, or instructions.
  microLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    lineHeight: 1.4,
    fontWeight: pwc.weight.semibold,
    letterSpacing: 0.2,
    color: tokens.color.text.secondary,
  } as CSSProperties,
  financialValue: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    lineHeight: 1.45,
    fontWeight: pwc.weight.regular,
    color: tokens.color.text.primary,
    fontVariantNumeric: "tabular-nums",
    textAlign: "right",
  } as CSSProperties,

  // --- Layout ------------------------------------------------------------
  // Canonical task-based page modes (design-system Layouts & density). The
  // app shell owns the route-level mode; a page may use a narrower inner
  // reading measure for prose but must not add another arbitrary page width.
  pageAuth: {
    width: "100%",
    maxWidth: tokens.layout.auth,
    margin: "0 auto",
  } as CSSProperties,
  pageForm: {
    width: "100%",
    maxWidth: tokens.layout.form,
    margin: "0 auto",
  } as CSSProperties,
  pageStandard: {
    width: "100%",
    maxWidth: tokens.layout.standard,
    margin: "0 auto",
  } as CSSProperties,
  pageWide: {
    width: "100%",
    maxWidth: tokens.layout.wideList,
    margin: "0 auto",
  } as CSSProperties,
  // Workspace mode — full available width (run report, Figures, PDF review).
  pageWorkspace: {
    width: "100%",
    maxWidth: "none",
  } as CSSProperties,
  toolbar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: pwc.space.md,
  } as CSSProperties,
  // Compact filter toolbar — controls flow left, denser gap than ui.toolbar.
  // Pair with the `quality-toolbar` class for narrow-screen stacking.
  filterToolbar: {
    display: "flex",
    alignItems: "flex-end",
    flexWrap: "wrap",
    gap: pwc.space.md,
  } as CSSProperties,
  // Standard section-header composition: title left, optional actions right,
  // sits above a section's content with a group gap below.
  sectionHeader: {
    display: "flex",
    alignItems: "baseline",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: pwc.space.md,
    marginBottom: pwc.space.lg,
  } as CSSProperties,
  stickyActionBar: {
    position: "sticky",
    bottom: 0,
    zIndex: 10,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    padding: pwc.space.lg,
    background: tokens.surface.default,
    borderTop: `1px solid ${tokens.color.border.subtle}`,
    boxShadow: pwc.shadow.elevated,
  } as CSSProperties,
  emptyState: {
    padding: `${pwc.space.xxl}px ${pwc.space.xl}px`,
    textAlign: "left",
    borderTop: `1px solid ${tokens.color.border.subtle}`,
    borderBottom: `1px solid ${tokens.color.border.subtle}`,
    color: tokens.color.text.secondary,
  } as CSSProperties,

  // Static card — a distinct object. FLAT: no shadow, no hover treatment.
  // Interactive (selectable/navigable) cards add className={uiClass.card}
  // for the quiet border/surface hover response — cards never lift.
  card: {
    background: tokens.surface.default,
    border: `1px solid ${tokens.color.border.subtle}`,
    borderRadius: tokens.radius.panel,
  } as CSSProperties,

  // Static bordered group — related controls or data, lighter than a card.
  // No elevation, no interaction treatment. Grouping order: alignment and
  // spacing → divider → inset surface → bordered group → interactive card.
  borderedGroup: {
    background: tokens.surface.default,
    border: `1px solid ${tokens.color.border.subtle}`,
    borderRadius: tokens.radius.panel,
    padding: pwc.space.xl,
  } as CSSProperties,

  fieldLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    color: tokens.color.text.secondary,
  } as CSSProperties,

  input: {
    ...controlBase,
    minHeight: 44,
    padding: `11px ${pwc.space.lg}px`,
  } as CSSProperties,

  select: {
    ...controlBase,
    minHeight: 44,
    padding: `11px ${pwc.space.lg}px`,
  } as CSSProperties,

  textarea: {
    ...controlBase,
    minHeight: 80,
    padding: `9px ${pwc.space.md}px`,
    resize: "vertical",
  } as CSSProperties,

  // --- Buttons -----------------------------------------------------------
  // Exactly four variants: Primary, Secondary, Quiet, Destructive. Primary
  // uses the accessible dark action role — signature orange stays an
  // identity/indicator colour, not a button fill. One dominant action per
  // decision region.
  buttonPrimary: {
    ...buttonBase,
    color: component.button.primary.text,
    background: component.button.primary.background,
    borderColor: component.button.primary.background,
  } as CSSProperties,

  buttonSecondary: {
    ...buttonBase,
    color: tokens.color.text.primary,
    background: tokens.surface.default,
    borderColor: tokens.color.border.strong,
  } as CSSProperties,

  buttonQuiet,
  // Deprecated aliases — Subtle and Ghost converged into the Quiet role.
  // Kept so existing call sites keep compiling while pages migrate to
  // ui.buttonQuiet; do not use in new code.
  buttonSubtle: buttonQuiet,
  buttonGhost: buttonQuiet,

  // Destructive action (delete / abort). Outline style so it stays quiet
  // until hovered — destructive buttons shouldn't compete with the primary
  // CTA for attention. Hover fill lives in index.css (.pwc-btn-danger).
  buttonDanger: {
    ...buttonBase,
    color: pwc.errorText,
    background: tokens.surface.default,
    borderColor: pwc.errorText,
  } as CSSProperties,

  // Size modifiers — spread after a variant: { ...ui.buttonPrimary, ...ui.buttonSm }
  // Compact desktop control (40px) — use only where controls are separated;
  // nothing interactive falls below the WCAG 24px minimum.
  buttonSm: {
    minHeight: 40,
    padding: "8px 14px",
    fontSize: 14,
  } as CSSProperties,

  buttonLg: {
    minHeight: 48,
    padding: "12px 24px",
    fontSize: 15,
  } as CSSProperties,

  // --- Monochrome status (design-system Status) ---------------------------
  // Routine status = neutral symbol + explicit text. No coloured dot, pill,
  // border, or fill. The symbol is aria-hidden; the text is the accessible
  // name. See components/StatusLabel.tsx and lib/runStatus.ts for the
  // canonical symbol families (○ ✓ ! × – ◇).
  status: {
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    lineHeight: 1.4,
    color: tokens.color.text.body,
    whiteSpace: "nowrap",
  } as CSSProperties,
  statusSymbol: {
    width: 14,
    flexShrink: 0,
    textAlign: "center",
    fontSize: 13,
    fontWeight: pwc.weight.semibold,
    color: tokens.color.text.secondary,
  } as CSSProperties,

  // --- Tabs ---------------------------------------------------------------
  // Shared underline tab geometry. Pages keep their own keyboard/selection
  // logic (role="tablist" etc.); these styles only carry the appearance.
  // Active = dark readable text + signature-orange indicator.
  tabBar: {
    display: "flex",
    gap: pwc.space.sm,
    borderBottom: `1px solid ${tokens.color.border.subtle}`,
    maxWidth: "100%",
    overflowX: "auto",
  } as CSSProperties,
  tab: {
    padding: "8px 16px",
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: pwc.weight.medium,
    background: "none",
    border: "none",
    borderBottom: "2px solid transparent",
    cursor: "pointer",
    color: tokens.color.text.secondary,
    marginBottom: -1,
    whiteSpace: "nowrap",
    textDecoration: "none",
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as CSSProperties,
  tabActive: {
    color: tokens.color.text.primary,
    borderBottomColor: tokens.color.brand.indicator,
  } as CSSProperties,

  // --- Dialog -------------------------------------------------------------
  // Shared modal geometry: semantic scrim + one elevated surface. Modal
  // shadows are the genuine-overlap exception to the flat-surface rule.
  scrim: {
    position: "fixed",
    inset: 0,
    background: component.dialog.scrim,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: pwc.space.xl,
    zIndex: 100,
  } as CSSProperties,
  dialog: {
    background: tokens.surface.default,
    border: `1px solid ${tokens.color.border.subtle}`,
    borderRadius: tokens.radius.panel,
    boxShadow: pwc.shadow.modal,
    width: "100%",
    maxWidth: 480,
    maxHeight: "85vh",
    overflow: "auto",
    padding: pwc.space.xl,
  } as CSSProperties,
  dialogTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 18,
    lineHeight: 1.3,
    fontWeight: pwc.weight.semibold,
    color: tokens.color.text.primary,
    margin: 0,
  } as CSSProperties,
  // One dominant action in the decision region: actions right-aligned,
  // destructive actions clearly separated by the consumer.
  dialogActionBar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: pwc.space.md,
    marginTop: pwc.space.xl,
  } as CSSProperties,

  // --- Badges ------------------------------------------------------------
  // Outline pills: `badge`/`badgeNeutral` keep the neutral grey300 border;
  // status variants override only the border hue. Labels stay neutral
  // (grey800); status is carried by the border + the paired dot.
  badge: {
    ...badgeBase,
  } as CSSProperties,
  badgeNeutral: {
    ...badgeBase,
  } as CSSProperties,
  badgeSuccess: {
    ...badgeBase,
    borderColor: pwc.success,
  } as CSSProperties,
  badgeWarning: {
    ...badgeBase,
    borderColor: pwc.warning,
  } as CSSProperties,
  badgeError: {
    ...badgeBase,
    borderColor: pwc.error,
  } as CSSProperties,
  badgeInfo: {
    ...badgeBase,
    borderColor: pwc.info,
  } as CSSProperties,
  badgeBrand: {
    ...badgeBase,
    borderColor: pwc.orange500,
  } as CSSProperties,

  // Leading status dot for an outline badge. Pass the status hue (use
  // pwc.grey500 for neutral). 7px circle per the design-system spec.
  badgeDot: (color: string): CSSProperties => ({
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: color,
    flexShrink: 0,
  }),

  // --- Alerts ------------------------------------------------------------
  // Neutral surface + a status-coloured left rule. Pair the icon with
  // ui.alertIcon(<hue>) so the icon (not a fill) carries the status.
  alertInfo: {
    ...alertBase,
    borderLeft: `3px solid ${pwc.info}`,
  } as CSSProperties,
  alertSuccess: {
    ...alertBase,
    borderLeft: `3px solid ${pwc.success}`,
  } as CSSProperties,
  alertWarning: {
    ...alertBase,
    borderLeft: `3px solid ${pwc.warning}`,
  } as CSSProperties,
  alertError: {
    ...alertBase,
    borderLeft: `3px solid ${pwc.error}`,
  } as CSSProperties,

  // Icon colour for an alert (the icon carries the status hue). 16px to match
  // the design-system alert icon size.
  alertIcon: (color: string): CSSProperties => ({
    color,
    fontSize: 16,
    lineHeight: 1.4,
    flexShrink: 0,
  }),

  // Dense inset box — the named home for the 12px inset used inside cards for
  // sub-sections / compact panels (layout normalization: promote the ad-hoc
  // 12px inset to a token so it stops being re-derived per component).
  cardInset: {
    padding: tokens.space.inset,
    border: `1px solid ${tokens.color.border.subtle}`,
    borderRadius: tokens.radius.control,
    background: tokens.surface.default,
  } as CSSProperties,

  // One KPI/stat tile at the canonical 16px padding (layout normalization:
  // replaces the 16 / 24 / 32 tiles scattered across StatTiles / EvalTab /
  // BenchmarksPage with a single shape). Quiet and FLAT — a metric is not an
  // elevated card.
  statTile: {
    padding: pwc.space.lg,
    border: `1px solid ${tokens.color.border.subtle}`,
    borderRadius: tokens.radius.control,
    background: tokens.surface.default,
    minWidth: 110,
  } as CSSProperties,

  // Icon button — the shared ≥32px hit-area primitive for glyph-only controls
  // (layout normalization: sub-32px icon buttons were an accessibility miss).
  iconButton: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    minWidth: 32,
    minHeight: 32,
    padding: pwc.space.xs,
    borderRadius: tokens.radius.control,
    border: "1px solid transparent",
    background: "transparent",
    color: tokens.color.text.secondary,
    cursor: "pointer",
  } as CSSProperties,

  // --- Table -------------------------------------------------------------
  // Three densities (design-system Tables): Compact 28–32px rows for
  // financial review · Standard 40px rows for operational lists ·
  // Comfortable 48px rows for setup and two-line content. A table's header
  // and body use the same density. Headers are sentence case.
  tableWrap: {
    border: `1px solid ${tokens.color.border.subtle}`,
    borderRadius: tokens.radius.control,
    overflow: "hidden",
  } as CSSProperties,
  // Standard density (40px rows).
  th: {
    ...thBase,
    padding: `10px ${pwc.space.lg}px`,
  } as CSSProperties,
  td: {
    padding: `10px ${pwc.space.lg}px`,
    borderBottom: `1px solid ${tokens.color.border.subtle}`,
  } as CSSProperties,
  // Comfortable density (48px rows) — setup surfaces, two-line content.
  thComfortable: {
    ...thBase,
    padding: `14px ${pwc.space.lg}px`,
  } as CSSProperties,
  tdComfortable: {
    padding: `14px ${pwc.space.lg}px`,
    borderBottom: `1px solid ${tokens.color.border.subtle}`,
  } as CSSProperties,

  // Compact density (28–32px rows) — data-dense tables (telemetry,
  // cross-checks, coverage, financial review).
  thDense: {
    ...thBase,
    fontSize: 12,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
  } as CSSProperties,
  tdDense: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
  } as CSSProperties,
};

// className hooks for states inline styles can't express (hover). The matching
// rules live in index.css. Focus rings are global and don't need a class.
export const uiClass = {
  btnPrimary: "pwc-btn-primary",
  btnSecondary: "pwc-btn-secondary",
  btnQuiet: "pwc-btn-quiet",
  // Deprecated aliases — both resolve to the Quiet hover rule.
  btnSubtle: "pwc-btn-quiet",
  btnGhost: "pwc-btn-quiet",
  btnDanger: "pwc-btn-danger",
  card: "pwc-card",
  tableRow: "pwc-table-row",
  tab: "pwc-tab",
} as const;
