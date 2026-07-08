import { pwc } from "../lib/theme";

// ---------------------------------------------------------------------------
// Skeleton — a grey placeholder bar in the shape of the coming content, used
// instead of a "Loading…" string while History rows, run-report tabs, and
// reviewer panels fetch (Phase 7). Shimmers grey100→grey50 via an opacity
// breathe; reduced-motion callers get a static bar (the global media block
// zeroes the animation). Compose several to sketch a row/card.
// ---------------------------------------------------------------------------

interface Props {
  /** CSS width — number (px) or any CSS length. Defaults to full width. */
  width?: number | string;
  /** Bar height in px. Defaults to a line of body text. */
  height?: number;
  /** Corner radius. Defaults to the small token; pass "50%" for an avatar. */
  radius?: number | string;
  style?: React.CSSProperties;
}

export function Skeleton({ width = "100%", height = 14, radius = pwc.radius.sm, style }: Props) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: "block",
        width,
        height,
        borderRadius: radius,
        background: pwc.grey100,
        animation: `skeleton-shimmer 1.4s ease-in-out infinite`,
        ...style,
      }}
    />
  );
}

// A stack of shimmer lines standing in for a loading text block / panel. The
// last line is shorter (like a real paragraph's final line). `role="status"`
// so assistive tech announces "loading" without a visible spinner.
export function SkeletonText({
  lines = 3,
  label = "Loading…",
  gap = 10,
}: {
  lines?: number;
  label?: string;
  gap?: number;
}) {
  return (
    <div role="status" aria-label={label} style={{ display: "flex", flexDirection: "column", gap }}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} width={i === lines - 1 ? "60%" : "100%"} />
      ))}
    </div>
  );
}
