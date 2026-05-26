import type { CSSProperties } from "react";
import { pwc } from "./theme";

// Shared inline component primitives. The app intentionally avoids Tailwind /
// className styling for Windows compatibility, so common UI language lives here
// instead of being recreated per page.

const controlBase: CSSProperties = {
  fontFamily: pwc.fontBody,
  fontSize: 13,
  lineHeight: 1.4,
  borderRadius: pwc.radius.sm,
  border: `1px solid ${pwc.grey300}`,
  background: pwc.white,
  color: pwc.grey900,
};

export const ui = {
  card: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    boxShadow: pwc.shadow.card,
  } as CSSProperties,

  fieldLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.grey700,
  } as CSSProperties,

  input: {
    ...controlBase,
    minHeight: 36,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
  } as CSSProperties,

  select: {
    ...controlBase,
    minHeight: 36,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
  } as CSSProperties,

  buttonPrimary: {
    minHeight: 36,
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.orange500,
    border: `1px solid ${pwc.orange500}`,
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
    textDecoration: "none",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: pwc.space.sm,
    whiteSpace: "nowrap",
  } as CSSProperties,

  buttonSecondary: {
    minHeight: 36,
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.grey900,
    background: pwc.white,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
    textDecoration: "none",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: pwc.space.sm,
    whiteSpace: "nowrap",
  } as CSSProperties,

  badge: {
    display: "inline-flex",
    alignItems: "center",
    minHeight: 24,
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.sm,
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: 600,
    whiteSpace: "nowrap",
  } as CSSProperties,
};
