import type { CSSProperties } from "react";
import { pwc } from "./theme";

// Shared inline component primitives, modelled on docs/pwc-design-system.html.
// The app intentionally avoids Tailwind / className styling for Windows
// compatibility (CLAUDE.md gotcha #7), so the common UI language lives here
// instead of being recreated per page.
//
// Hover / focus states can't be expressed inline. Components opt into the
// matching global rules in index.css by also setting `className` to one of
// the `uiClass` constants below (e.g. `<button className={uiClass.btnPrimary}
// style={ui.buttonPrimary}>`). Form-control focus rings are applied globally
// to all inputs/selects/textareas in index.css and need no className.

const controlBase: CSSProperties = {
  fontFamily: pwc.fontBody,
  fontSize: 15,
  lineHeight: 1.45,
  borderRadius: pwc.radius.lg,
  border: `1px solid ${pwc.grey300}`,
  background: pwc.white,
  color: pwc.grey900,
};

// Shared button geometry. Variants differ only in colour.
const buttonBase: CSSProperties = {
  minHeight: 40,
  padding: `10px ${pwc.space.xl}px`,
  fontFamily: pwc.fontHeading,
  fontSize: 15,
  fontWeight: pwc.weight.medium,
  borderRadius: pwc.radius.lg,
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
// border + matching dot, neutral grey label. Variants override only borderColor;
// pair with `ui.badgeDot(<hue>)` for the leading dot.
const badgeBase: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 7,
  minHeight: 22,
  padding: "3px 11px",
  borderRadius: pwc.radius.pill,
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
  borderRadius: pwc.radius.md,
  background: pwc.white,
  border: `1px solid ${pwc.grey200}`,
  borderLeft: `3px solid ${pwc.grey300}`,
  color: pwc.grey800,
  fontFamily: pwc.fontBody,
  fontSize: 15,
  lineHeight: 1.55,
};

export const ui = {
  // --- Typography --------------------------------------------------------
  // Semantic roles keep page-level hierarchy consistent without pretending
  // that every product surface has identical density. Components may still
  // use the compact roles in data-heavy workspaces.
  pageTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 32,
    lineHeight: 1.1,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
    margin: 0,
  } as CSSProperties,
  pageTitleCompact: {
    fontFamily: pwc.fontHeading,
    fontSize: 22,
    lineHeight: 1.2,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
    margin: 0,
  } as CSSProperties,
  sectionTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 20,
    lineHeight: 1.25,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
    margin: 0,
  } as CSSProperties,
  subsectionTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    lineHeight: 1.35,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
    margin: 0,
  } as CSSProperties,
  bodyText: {
    fontFamily: pwc.fontBody,
    fontSize: 15,
    lineHeight: 1.55,
    fontWeight: pwc.weight.regular,
    color: pwc.grey800,
  } as CSSProperties,
  supportingText: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    lineHeight: 1.5,
    fontWeight: pwc.weight.regular,
    color: pwc.grey700,
  } as CSSProperties,
  metadata: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    lineHeight: 1.45,
    fontWeight: pwc.weight.regular,
    color: pwc.grey500,
  } as CSSProperties,
  financialValue: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    lineHeight: 1.45,
    fontWeight: pwc.weight.regular,
    color: pwc.grey900,
    fontVariantNumeric: "tabular-nums",
    textAlign: "right",
  } as CSSProperties,

  // --- Layout ------------------------------------------------------------
  pageForm: {
    width: "100%",
    maxWidth: 840,
    margin: "0 auto",
  } as CSSProperties,
  pageWide: {
    width: "100%",
    maxWidth: 1440,
    margin: "0 auto",
  } as CSSProperties,
  toolbar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: pwc.space.md,
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
    background: pwc.white,
    borderTop: `1px solid ${pwc.grey200}`,
    boxShadow: pwc.shadow.elevated,
  } as CSSProperties,
  emptyState: {
    padding: `${pwc.space.xxl}px ${pwc.space.xl}px`,
    textAlign: "left",
    borderTop: `1px solid ${pwc.grey200}`,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey700,
  } as CSSProperties,

  card: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    boxShadow: pwc.shadow.card,
  } as CSSProperties,

  fieldLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    color: pwc.grey700,
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
  buttonPrimary: {
    ...buttonBase,
    color: pwc.white,
    background: pwc.orange500,
    borderColor: pwc.orange500,
  } as CSSProperties,

  buttonSecondary: {
    ...buttonBase,
    color: pwc.grey900,
    background: pwc.white,
    borderColor: pwc.grey300,
  } as CSSProperties,

  buttonSubtle: {
    ...buttonBase,
    color: pwc.grey900,
    background: pwc.grey100,
  } as CSSProperties,

  buttonGhost: {
    ...buttonBase,
    color: pwc.orange500,
    background: "transparent",
  } as CSSProperties,

  // Destructive action (delete / abort). Outline style so it stays quiet
  // until hovered — destructive buttons shouldn't compete with the primary
  // CTA for attention. Hover fill lives in index.css (.pwc-btn-danger).
  buttonDanger: {
    ...buttonBase,
    color: pwc.error,
    background: pwc.white,
    borderColor: pwc.error,
  } as CSSProperties,

  // Size modifiers — spread after a variant: { ...ui.buttonPrimary, ...ui.buttonSm }
  buttonSm: {
    minHeight: 36,
    padding: "8px 16px",
    fontSize: 14,
  } as CSSProperties,

  buttonLg: {
    minHeight: 44,
    padding: "12px 24px",
    fontSize: 15,
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
    padding: pwc.space.md,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    background: pwc.white,
  } as CSSProperties,

  // One KPI/stat tile at the canonical 16px padding (layout normalization:
  // replaces the 16 / 24 / 32 tiles scattered across StatTiles / EvalTab /
  // BenchmarksPage with a single shape).
  statTile: {
    padding: pwc.space.lg,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    background: pwc.white,
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
    borderRadius: pwc.radius.md,
    border: "1px solid transparent",
    background: "transparent",
    color: pwc.grey700,
    cursor: "pointer",
  } as CSSProperties,

  // --- Table -------------------------------------------------------------
  tableWrap: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    overflow: "hidden",
  } as CSSProperties,
  th: {
    textAlign: "left",
    padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
    background: pwc.grey100,
    fontSize: 13,
    textTransform: "uppercase",
    // Workspace convention keeps letter-spacing at 0 (the design-system
    // reference shows .8px on table headers, but local guidance wins here).
    letterSpacing: 0,
    color: pwc.grey500,
    fontWeight: pwc.weight.semibold,
    borderBottom: `1px solid ${pwc.grey200}`,
  } as CSSProperties,
  td: {
    padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
  } as CSSProperties,

  // Dense table cells (8/12) — ONE shared compact variant for data-dense
  // tables (telemetry, cross-checks, coverage), replacing the bespoke
  // 4/8 · 8/12 · 0/6 paddings each of those grew independently.
  thDense: {
    textAlign: "left",
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.grey100,
    fontSize: 12,
    textTransform: "uppercase",
    letterSpacing: 0,
    color: pwc.grey500,
    fontWeight: pwc.weight.semibold,
    borderBottom: `1px solid ${pwc.grey200}`,
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
  btnSubtle: "pwc-btn-subtle",
  btnGhost: "pwc-btn-ghost",
  btnDanger: "pwc-btn-danger",
  card: "pwc-card",
  tableRow: "pwc-table-row",
} as const;
