import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AgentTabStatus, ReasoningBlock, SSEEvent, ToolTimelineEntry } from "../lib/types";
import { buildActivitySentences } from "../lib/buildActivitySentences";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";

interface Props {
  events: SSEEvent[];
  toolTimeline: ToolTimelineEntry[];
  reasoningBlocks: ReasoningBlock[];
  isRunning: boolean;
  status?: AgentTabStatus;
  /** Identifies the stream shown; a new stream opens at its latest update. */
  streamKey: string;
  recorded?: boolean;
  /** Tool calls, model and usage for this workstream. When supplied, one
   *  "Technical detail" checkbox swaps the plain-language stream for it, so
   *  the operator never has to compare two activity panels. */
  technical?: ReactNode;
  onTechnicalChange?: (open: boolean) => void;
}

interface UpdateProps {
  id: string;
  text: string;
  active: boolean;
  isRunning: boolean;
  isLatest: boolean;
}

const ActivityUpdate = memo(function ActivityUpdate({
  id,
  text,
  active,
  isRunning,
  isLatest,
}: UpdateProps) {
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
        <p style={styles.sentence}>{text}</p>
      </div>
    </li>
  );
});

export function ActivityStream({
  events,
  toolTimeline,
  reasoningBlocks,
  isRunning,
  status,
  streamKey,
  recorded = false,
  technical,
  onTechnicalChange,
}: Props) {
  const [showTechnical, setShowTechnical] = useState(false);
  // Newest first: the latest update sits at the top, so following live
  // work never needs scrolling, and opening Activity never moves the page.
  const items = useMemo(
    () => buildActivitySentences(events, toolTimeline, status),
    [events, toolTimeline, reasoningBlocks, status],
  );
  const announcement = items[0]?.text ?? "";
  // Switching to another stream starts at its latest update.
  const feedRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = 0;
  }, [streamKey]);

  return (
    <section aria-label={recorded ? "Recorded activity" : "Live activity"} style={styles.root}>
      <span
        role="status"
        aria-label="Current agent activity"
        aria-live="polite"
        style={styles.visuallyHidden}
      >
        {announcement}
      </span>
      <div style={styles.headingRow}>
        <div style={styles.heading}>{recorded ? "Recorded activity" : "Live activity"}</div>
        {technical != null && (
          <label style={styles.technicalToggle}>
            <input type="checkbox" style={ui.checkbox} checked={showTechnical}
              onChange={(event) => { setShowTechnical(event.target.checked); onTechnicalChange?.(event.target.checked); }} />
            Technical detail
          </label>
        )}
      </div>
      {showTechnical && technical != null ? <div data-testid="activity-technical">{technical}</div> : <ol
        ref={feedRef}
        className="agent-scroll"
        aria-label="Activity updates"
        tabIndex={0}
        style={styles.feed}
      >
        {items.length === 0 ? (
          <li data-testid="activity-empty" style={styles.empty}>{recorded ? "No activity was recorded for this workstream." : "Waiting for the next update…"}</li>
        ) : items.map((item, index) => (
            <ActivityUpdate
              key={item.id}
              id={item.id}
              text={item.text}
              active={item.active}
              isRunning={isRunning}
              isLatest={index === 0}
            />
        ))}
      </ol>}
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
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    minHeight: 40,
    marginBottom: pwc.space.sm,
  } as const,
  heading: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
  } as const,
  technicalToggle: {
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
    cursor: "pointer",
  } as const,
  // The list grows with the page; only narrow layouts bound it.
  feed: {
    margin: 0,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px ${pwc.space.xs}px 0`,
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
