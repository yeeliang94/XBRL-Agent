import { useId, useState } from "react";
import { ds, tokens } from "../lib/theme";
import { DisclosureChevron } from "./icons";

// ---------------------------------------------------------------------------
// Disclosure — the one expand-to-view primitive behind every collapsed
// developer surface (Technical details, Performance details, AI usage,
// Diagnostics, Advanced settings). Collapsed by default; the chevron follows
// the title, rotates 90°, and the body reveals at motion.duration.base. The operator's stated
// preference: engineering detail stays one click away, never in the way.
//
// Uncontrolled by default (own open state); pass `open`/`onToggle` to control.
// ---------------------------------------------------------------------------

interface Props {
  /** The always-visible summary line (the clickable trigger). */
  summary: React.ReactNode;
  children: React.ReactNode;
  /** Start expanded. Ignored when controlled via `open`. */
  defaultOpen?: boolean;
  /** Controlled open state (pair with onToggle). */
  open?: boolean;
  onToggle?: (open: boolean) => void;
  /** Optional style overrides for the outer wrapper. */
  style?: React.CSSProperties;
}

export function Disclosure({ summary, children, defaultOpen = false, open, onToggle, style }: Props) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : internalOpen;
  const bodyId = useId();

  const toggle = () => {
    const next = !isOpen;
    if (!isControlled) setInternalOpen(next);
    onToggle?.(next);
  };

  return (
    <div style={style}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={isOpen}
        aria-controls={bodyId}
        style={{
          display: "flex",
          alignItems: "center",
          gap: ds.space.sm,
          width: "100%",
          padding: `${ds.space.sm}px 0`,
          background: "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          fontFamily: ds.fontHeading,
          fontSize: 14,
          fontWeight: ds.weight.medium,
          color: tokens.color.text.primary,
        }}
      >
        {summary}
        <DisclosureChevron open={isOpen} />
      </button>
      {isOpen && (
        <div
          id={bodyId}
          className="app-disclosure-content"
          style={{
            overflow: "hidden",
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}
