import { memo, useCallback, useEffect, useMemo, useRef } from "react";
import type { ReasoningBlock, SSEEvent, ToolTimelineEntry } from "../lib/types";
import { buildActivitySentences } from "../lib/buildActivitySentences";
import { useFrameBatchedText } from "../lib/useFrameBatchedText";
import { pwc } from "../lib/theme";

interface Props {
  events: SSEEvent[];
  toolTimeline: ToolTimelineEntry[];
  reasoningBlocks: ReasoningBlock[];
  isRunning: boolean;
  streamKey: string;
}

interface UpdateProps {
  id: string;
  text: string;
  source: "reasoning" | "status" | "tool";
  active: boolean;
  isRunning: boolean;
  isLatest: boolean;
  showProviderLabel: boolean;
}

const ActivityUpdate = memo(function ActivityUpdate({
  id,
  text,
  source,
  active,
  isRunning,
  isLatest,
  showProviderLabel,
}: UpdateProps) {
  const displayedText = useFrameBatchedText(
    text,
    Boolean(active && source === "reasoning" && isRunning),
  );

  return (
    <li
      className="activity-sentence-enter"
      data-testid="activity-sentence"
      data-activity-id={id}
      aria-current={isLatest ? "true" : undefined}
      style={styles.update}
    >
      <span aria-hidden="true" style={styles.rail}>
        <span
          className={active && isRunning ? "activity-sentence-pulse" : undefined}
          style={{ ...styles.dot, ...(active && isRunning ? styles.dotActive : styles.dotComplete) }}
        />
      </span>
      <div style={styles.copy}>
        {showProviderLabel ? (
          <span style={styles.source}>Provider reasoning</span>
        ) : null}
        <p style={styles.sentence}>{displayedText}</p>
      </div>
    </li>
  );
});

function reasoningGroupId(id: string): string | null {
  if (!id.startsWith("reasoning:")) return null;
  const sentenceSeparator = id.lastIndexOf(":");
  return sentenceSeparator > 0 ? id.slice(0, sentenceSeparator) : id;
}

export function ActivityStream({
  events,
  toolTimeline,
  reasoningBlocks,
  isRunning,
  streamKey,
}: Props) {
  const items = useMemo(
    () => buildActivitySentences(events, toolTimeline, reasoningBlocks).reverse(),
    [events, toolTimeline, reasoningBlocks],
  );
  const scrollRef = useRef<HTMLOListElement>(null);
  const followLatestRef = useRef(true);
  const previousStreamKeyRef = useRef(streamKey);
  const latest = items[items.length - 1] ?? null;
  const followKey = `${latest?.id ?? "empty"}:${latest?.text.length ?? 0}`;

  let announcement = "";
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].source !== "reasoning") {
      announcement = items[index].text;
      break;
    }
  }

  useEffect(() => {
    if (previousStreamKeyRef.current !== streamKey) {
      previousStreamKeyRef.current = streamKey;
      followLatestRef.current = true;
    }
    const node = scrollRef.current;
    if (node && followLatestRef.current) {
      node.scrollTop = node.scrollHeight;
    }
  }, [followKey, streamKey]);

  const handleScroll = useCallback(() => {
    const node = scrollRef.current;
    if (!node) return;
    const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
    followLatestRef.current = distanceFromBottom <= 32;
  }, []);

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
      <div style={styles.heading}>Live activity</div>
      <ol
        ref={scrollRef}
        className="agent-scroll"
        aria-label="Activity updates"
        tabIndex={0}
        onScroll={handleScroll}
        style={styles.feed}
      >
        {items.length === 0 ? (
          <li data-testid="activity-empty" style={styles.empty}>Waiting for the next update…</li>
        ) : items.map((item, index) => {
          const previous = items[index - 1];
          const group = reasoningGroupId(item.id);
          const previousGroup = previous ? reasoningGroupId(previous.id) : null;
          return (
            <ActivityUpdate
              key={item.id}
              id={item.id}
              text={item.text}
              source={item.source}
              active={item.active}
              isRunning={isRunning}
              isLatest={index === items.length - 1}
              showProviderLabel={group !== null && group !== previousGroup}
            />
          );
        })}
      </ol>
    </section>
  );
}

const styles = {
  root: {
    minWidth: 0,
    padding: `${pwc.space.lg}px 0 ${pwc.space.sm}px`,
    borderTop: `1px solid ${pwc.grey100}`,
  } as const,
  heading: {
    minHeight: 24,
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey700,
    textTransform: "uppercase" as const,
    letterSpacing: "0.02em",
  } as const,
  feed: {
    maxHeight: "min(52vh, 520px)",
    margin: 0,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px ${pwc.space.xs}px 0`,
    overflowY: "auto" as const,
    overscrollBehavior: "contain" as const,
    listStyle: "none",
    scrollBehavior: "auto" as const,
  } as const,
  update: {
    display: "grid",
    gridTemplateColumns: "10px minmax(0, 1fr)",
    gap: pwc.space.sm,
    alignItems: "stretch",
    minHeight: 48,
    padding: `${pwc.space.xs}px 0 ${pwc.space.md}px`,
    contentVisibility: "auto" as const,
    containIntrinsicSize: "0 56px",
  } as const,
  rail: {
    position: "relative" as const,
    width: 10,
    marginLeft: 3,
    borderLeft: `1px solid ${pwc.grey200}`,
  } as const,
  dot: {
    position: "absolute" as const,
    top: 6,
    left: -4,
    width: 7,
    height: 7,
    borderRadius: "50%",
  } as const,
  dotActive: {
    background: pwc.orange500,
  } as const,
  dotComplete: {
    background: pwc.grey300,
  } as const,
  copy: {
    minWidth: 0,
  } as const,
  source: {
    display: "block",
    marginBottom: 2,
    fontFamily: pwc.fontBody,
    fontSize: 10,
    color: pwc.grey500,
  } as const,
  sentence: {
    margin: 0,
    maxWidth: 680,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    lineHeight: 1.55,
    fontWeight: pwc.weight.regular,
    color: pwc.grey800,
    whiteSpace: "pre-wrap" as const,
    overflowWrap: "anywhere" as const,
  } as const,
  empty: {
    padding: `${pwc.space.md}px 0`,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey500,
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
