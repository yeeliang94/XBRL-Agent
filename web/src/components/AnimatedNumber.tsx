import { useEffect, useRef, useState } from "react";
import { pwc } from "../lib/theme";

// ---------------------------------------------------------------------------
// AnimatedNumber — a number that counts from its previous on-screen value to a
// new one when it CHANGES, within the app's motion budget (pwc.motion). It is
// the one number-transition primitive; stat tiles, eval scores and telemetry
// figures all read through it.
//
// Two rules keep it calm and test-safe:
//   1. First mount shows the final value INSTANTLY — a static historical run
//      must never roll up from zero on load (PLAN-motion-transitions §7). Only
//      an in-session value change animates.
//   2. prefers-reduced-motion (and any environment without matchMedia / rAF,
//      e.g. jsdom under test or SSR) snaps straight to the target.
// The global reduced-motion block in index.css covers CSS motion; this JS
// tween has to honour the same preference itself, hence the explicit check.
// ---------------------------------------------------------------------------

interface Props {
  value: number;
  /** Formats the (already rounded) number for display. Default: en thousands. */
  format?: (n: number) => string;
  /** Round each animated frame to an integer (counts). false keeps decimals. */
  integer?: boolean;
  style?: React.CSSProperties;
  "data-testid"?: string;
}

// The tween runs at the "slow" token (250ms) — the longest in the budget,
// because a number rolling reads best with a touch more room than a fade.
const DURATION_MS = parseInt(pwc.motion.duration.slow, 10) || 250;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

// Decelerate, no overshoot — the JS analogue of pwc.motion.easing
// (cubic-bezier(0.2, 0, 0, 1)). ease-out cubic is visually indistinguishable
// here and needs no bezier solver.
function easeOut(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

export function AnimatedNumber({
  value,
  format,
  integer = true,
  style,
  "data-testid": testId,
}: Props) {
  const fmt = format ?? ((n: number) => n.toLocaleString());
  const [display, setDisplay] = useState(value);
  // Live mirror of what's on screen, so an interrupted tween resumes from the
  // current value rather than jumping back to the previous target.
  const displayRef = useRef(value);
  const rafRef = useRef<number | null>(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    displayRef.current = display;
  }, [display]);

  useEffect(() => {
    // Rule 1: never animate the first paint.
    if (!mountedRef.current) {
      mountedRef.current = true;
      displayRef.current = value;
      setDisplay(value);
      return;
    }
    // Rule 2: snap when motion is unwanted or unavailable.
    if (prefersReducedMotion() || typeof requestAnimationFrame !== "function") {
      displayRef.current = value;
      setDisplay(value);
      return;
    }

    const from = displayRef.current;
    const to = value;
    if (from === to) return;

    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / DURATION_MS);
      if (t >= 1) {
        displayRef.current = to;
        setDisplay(to);
        rafRef.current = null;
        return;
      }
      setDisplay(from + (to - from) * easeOut(t));
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [value]);

  const shown = integer ? Math.round(display) : display;
  return (
    <span style={style} data-testid={testId}>
      {fmt(shown)}
    </span>
  );
}
