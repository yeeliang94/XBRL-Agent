import { useEffect, useMemo, useState } from "react";
import type { ReasoningBlock, SSEEvent, ToolTimelineEntry } from "../lib/types";
import { buildActivitySentences } from "../lib/buildActivitySentences";
import { useFrameBatchedText } from "../lib/useFrameBatchedText";
import { pwc } from "../lib/theme";

interface Props {
  events: SSEEvent[];
  toolTimeline: ToolTimelineEntry[];
  reasoningBlocks: ReasoningBlock[];
  isRunning: boolean;
}

export function ActivitySentenceCarousel({
  events,
  toolTimeline,
  reasoningBlocks,
  isRunning,
}: Props) {
  const items = useMemo(
    () => buildActivitySentences(events, toolTimeline, reasoningBlocks),
    [events, toolTimeline, reasoningBlocks],
  );
  const [index, setIndex] = useState(0);
  const latestId = items[0]?.id ?? null;

  // A genuinely new event becomes the visible sentence. Streaming deltas keep
  // the same id, so they do not pull an operator away from an older item they
  // deliberately selected.
  useEffect(() => {
    setIndex(0);
  }, [latestId]);

  const safeIndex = Math.min(index, Math.max(0, items.length - 1));
  const item = items[safeIndex] ?? null;
  const displayedText = useFrameBatchedText(
    item?.text ?? "Waiting for the next update…",
    Boolean(item?.active && item.source === "reasoning" && isRunning),
  );
  const announcement = items.find((candidate) => candidate.source !== "reasoning")?.text ?? "";

  return (
    <section aria-label="Live activity" style={styles.root}>
      <span
        role="status"
        aria-label="Current agent activity"
        aria-live="polite"
        style={styles.visuallyHidden}
      >
        {announcement}
      </span>
      <div style={styles.headingRow}>
        <span style={styles.heading}>Live activity</span>
        {item ? (
          <span style={styles.position}>{safeIndex + 1} / {items.length}</span>
        ) : null}
      </div>
      <div
        key={item?.id ?? "waiting"}
        className="activity-sentence-enter"
        data-testid="activity-sentence"
        style={styles.sentenceRow}
      >
        <span
          aria-hidden="true"
          className={item?.active && isRunning ? "activity-sentence-pulse" : undefined}
          style={styles.dot}
        />
        <div style={styles.copy}>
          <span style={styles.source}>
            {item?.source === "reasoning" ? "Reasoning · Provider-supplied" : "Activity"}
          </span>
          <p style={styles.sentence}>{displayedText}</p>
        </div>
      </div>
      {items.length > 1 ? (
        <div aria-label="Activity sentence controls" style={styles.controls}>
          <button
            type="button"
            className="activity-carousel-control"
            aria-label="Show older activity"
            disabled={safeIndex >= items.length - 1}
            onClick={() => setIndex((current) => Math.min(items.length - 1, current + 1))}
            style={styles.controlButton}
          >
            ‹
          </button>
          <button
            type="button"
            className="activity-carousel-control"
            aria-label="Show newer activity"
            disabled={safeIndex === 0}
            onClick={() => setIndex((current) => Math.max(0, current - 1))}
            style={styles.controlButton}
          >
            ›
          </button>
        </div>
      ) : null}
    </section>
  );
}

const styles = {
  root: {
    minWidth: 0,
    padding: `${pwc.space.lg}px 0 ${pwc.space.sm}px`,
    borderTop: `1px solid ${pwc.grey100}`,
  } as const,
  headingRow: {
    minHeight: 24,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
  } as const,
  heading: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey700,
    textTransform: "uppercase" as const,
    letterSpacing: "0.02em",
  } as const,
  position: {
    fontFamily: pwc.fontMono,
    fontSize: 10,
    color: pwc.grey400,
  } as const,
  sentenceRow: {
    minHeight: 104,
    display: "grid",
    gridTemplateColumns: "10px minmax(0, 1fr)",
    gap: pwc.space.sm,
    alignItems: "start",
    padding: `${pwc.space.md}px 0 ${pwc.space.sm}px`,
  } as const,
  dot: {
    width: 6,
    height: 6,
    marginTop: 6,
    borderRadius: "50%",
    background: pwc.orange500,
  } as const,
  copy: {
    minWidth: 0,
  } as const,
  source: {
    display: "block",
    marginBottom: pwc.space.xs,
    fontFamily: pwc.fontBody,
    fontSize: 10,
    color: pwc.grey500,
  } as const,
  sentence: {
    margin: 0,
    maxWidth: 680,
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    lineHeight: 1.65,
    fontWeight: pwc.weight.regular,
    color: pwc.grey800,
    whiteSpace: "pre-wrap" as const,
    overflowWrap: "anywhere" as const,
  } as const,
  controls: {
    display: "flex",
    justifyContent: "flex-end",
    gap: 2,
  } as const,
  controlButton: {
    width: 28,
    height: 28,
    padding: 0,
    border: "none",
    borderRadius: 0,
    background: "transparent",
    color: pwc.grey500,
    fontFamily: pwc.fontBody,
    fontSize: 20,
    lineHeight: 1,
    cursor: "pointer",
  } as const,
  visuallyHidden: {
    position: "absolute",
    width: 1,
    height: 1,
    padding: 0,
    margin: -1,
    overflow: "hidden",
    clip: "rect(0, 0, 0, 0)",
    whiteSpace: "nowrap" as const,
    border: 0,
  } as const,
};
