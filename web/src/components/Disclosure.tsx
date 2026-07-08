import { useId, useState } from "react";
import { pwc } from "../lib/theme";

// ---------------------------------------------------------------------------
// Disclosure — the one expand-to-view primitive behind every collapsed
// developer surface (Technical details, Performance details, AI usage,
// Diagnostics, Advanced settings). Collapsed by default; a chevron rotates 90°
// and the body reveals at motion.duration.base. The operator's stated
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
          gap: pwc.space.sm,
          width: "100%",
          padding: `${pwc.space.sm}px 0`,
          background: "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          fontFamily: pwc.fontHeading,
          fontSize: 14,
          fontWeight: pwc.weight.medium,
          color: pwc.grey700,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            transform: isOpen ? "rotate(90deg)" : "rotate(0deg)",
            transition: `transform ${pwc.motion.duration.base} ${pwc.motion.easing}`,
            fontSize: 12,
            color: pwc.grey500,
          }}
        >
          ▶
        </span>
        {summary}
      </button>
      {isOpen && (
        <div
          id={bodyId}
          style={{
            // Slide open — animate max-height/opacity; the existing slide-down
            // keyframe caps at 500px, which is fine for these developer panels.
            overflow: "hidden",
            animation: `slide-down ${pwc.motion.duration.base} ${pwc.motion.easing}`,
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}
