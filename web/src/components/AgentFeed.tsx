import { useState, useEffect, useRef, useCallback } from "react";
import type {
  SSEEvent,
  ThinkingBlock as ThinkingBlockType,
  ToolTimelineEntry,
  EventPhase,
  StatusData,
  ToolCallData,
  ToolResultData,
} from "../lib/types";
import { pwc } from "../lib/theme";
import { ThinkingBlock } from "./ThinkingBlock";
import { ToolCallCard } from "./ToolCallCard";
import { StreamingText } from "./StreamingText";

interface Props {
  events: SSEEvent[];
  thinkingBlocks: ThinkingBlockType[];
  toolTimeline: ToolTimelineEntry[];
  streamingText: string;
  thinkingBuffer: string;
  activeThinkingId: string | null;
  isRunning: boolean;
  currentPhase: EventPhase | null;
}

type ViewMode = "timeline" | "raw";

const styles = {
  container: {
    background: pwc.white,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    boxShadow: pwc.shadow.card,
    overflow: "hidden",
  } as React.CSSProperties,
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    background: pwc.grey50,
  } as React.CSSProperties,
  title: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    fontSize: 14,
    color: pwc.grey700,
  } as React.CSSProperties,
  togglePill: {
    display: "flex",
    background: pwc.grey100,
    borderRadius: pwc.radius.md,
    overflow: "hidden",
  } as React.CSSProperties,
  toggleActive: {
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontSize: 12,
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    background: pwc.orange500,
    color: pwc.white,
    border: "none",
    cursor: "pointer",
  } as React.CSSProperties,
  toggleInactive: {
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontSize: 12,
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    background: pwc.white,
    color: pwc.grey700,
    border: "none",
    cursor: "pointer",
    transition: "background 0.15s",
  } as React.CSSProperties,
  scrollArea: {
    maxHeight: 480,
    overflowY: "auto" as const,
    padding: pwc.space.lg,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  // Phase divider
  phaseDivider: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    margin: `${pwc.space.sm}px 0`,
  } as React.CSSProperties,
  phaseLine: {
    flex: 1,
    height: 1,
    background: pwc.grey200,
  } as React.CSSProperties,
  phasePill: {
    fontFamily: pwc.fontMono,
    fontSize: 11,
    color: pwc.grey500,
    background: pwc.grey100,
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.sm,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  // Raw log
  rawEvent: {
    display: "flex",
    alignItems: "flex-start",
    gap: pwc.space.md,
    fontSize: 13,
  } as React.CSSProperties,
  rawBadge: {
    fontFamily: pwc.fontMono,
    fontSize: 11,
    padding: "2px 6px",
    borderRadius: pwc.radius.sm,
    background: pwc.grey100,
    color: pwc.grey700,
    whiteSpace: "nowrap" as const,
    flexShrink: 0,
  } as React.CSSProperties,
  rawContent: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey800,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    flex: 1,
  } as React.CSSProperties,
};

// Badge color overrides for raw log mode
const RAW_BADGE_COLORS: Record<string, React.CSSProperties> = {
  status: { background: "#DBEAFE", color: "#1E40AF" },
  tool_call: { background: "#F3E8FF", color: "#6B21A8" },
  tool_result: { background: "#DCFCE7", color: "#166534" },
  error: { background: "#FEE2E2", color: "#991B1B" },
  complete: { background: "#D1FAE5", color: "#065F46" },
  text_delta: { background: "#F1F5F9", color: "#334155" },
  thinking_delta: { background: "#FEF3C7", color: "#92400E" },
  thinking_end: { background: "#FEF3C7", color: "#78350F" },
};

export function AgentFeed({
  events,
  thinkingBlocks,
  toolTimeline,
  streamingText,
  thinkingBuffer,
  activeThinkingId,
  isRunning,
}: Props) {
  const [viewMode, setViewMode] = useState<ViewMode>("timeline");
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  // Auto-scroll to bottom, unless user scrolled up
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    userScrolledUp.current = !atBottom;
  }, []);

  useEffect(() => {
    if (!userScrolledUp.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events.length, thinkingBlocks.length, toolTimeline.length, streamingText]);

  // Build timeline items from events for phase dividers
  const phaseChanges = events
    .filter((e) => e.event === "status")
    .map((e) => (e.data as StatusData).phase);
  const uniquePhases = [...new Set(phaseChanges)];

  return (
    <div style={styles.container}>
      {/* Header with toggle */}
      <div style={styles.header}>
        <span style={styles.title}>
          Agent Activity ({events.filter((e) => e.event !== "token_update").length} events)
        </span>
        <div style={styles.togglePill}>
          <button
            onClick={() => setViewMode("timeline")}
            style={viewMode === "timeline" ? styles.toggleActive : styles.toggleInactive}
          >
            Timeline
          </button>
          <button
            onClick={() => setViewMode("raw")}
            style={viewMode === "raw" ? styles.toggleActive : styles.toggleInactive}
          >
            Raw Log
          </button>
        </div>
      </div>

      {/* Scroll area */}
      <div ref={scrollRef} className="agent-scroll" style={styles.scrollArea} onScroll={handleScroll}>
        {viewMode === "timeline" ? (
          <TimelineView
            thinkingBlocks={thinkingBlocks}
            toolTimeline={toolTimeline}
            streamingText={streamingText}
            thinkingBuffer={thinkingBuffer}
            activeThinkingId={activeThinkingId}
            isRunning={isRunning}
            phases={uniquePhases}
          />
        ) : (
          <RawLogView events={events} />
        )}
      </div>
    </div>
  );
}

// --- Timeline View ---

function TimelineView({
  thinkingBlocks,
  toolTimeline,
  streamingText,
  thinkingBuffer,
  activeThinkingId,
  isRunning,
  phases,
}: {
  thinkingBlocks: ThinkingBlockType[];
  toolTimeline: ToolTimelineEntry[];
  streamingText: string;
  thinkingBuffer: string;
  activeThinkingId: string | null;
  isRunning: boolean;
  phases: EventPhase[];
}) {
  // Build a chronological list of renderable items
  type TimelineItem =
    | { type: "phase"; phase: EventPhase }
    | { type: "thinking"; block: ThinkingBlockType }
    | { type: "tool"; entry: ToolTimelineEntry }
    | { type: "streaming_thinking" }
    | { type: "text" };

  const items: TimelineItem[] = [];
  let lastPhase: EventPhase | null = null;

  // Interleave thinking blocks and tool entries by timestamp
  const allEntries: { ts: number; item: TimelineItem }[] = [];

  for (const block of thinkingBlocks) {
    // Add phase divider if phase changed
    if (block.phase && block.phase !== lastPhase) {
      allEntries.push({ ts: block.timestamp - 1, item: { type: "phase", phase: block.phase } });
      lastPhase = block.phase;
    }
    allEntries.push({ ts: block.timestamp, item: { type: "thinking", block } });
  }

  for (const entry of toolTimeline) {
    if (entry.phase && entry.phase !== lastPhase) {
      allEntries.push({ ts: entry.startTime - 1, item: { type: "phase", phase: entry.phase } });
      lastPhase = entry.phase;
    }
    allEntries.push({ ts: entry.startTime, item: { type: "tool", entry } });
  }

  // Sort by timestamp
  allEntries.sort((a, b) => a.ts - b.ts);

  // Deduplicate phase dividers
  const seenPhases = new Set<EventPhase>();
  for (const { item } of allEntries) {
    if (item.type === "phase") {
      if (seenPhases.has(item.phase)) continue;
      seenPhases.add(item.phase);
    }
    items.push(item);
  }

  // If no items built from blocks/timeline but phases exist, show phase dividers
  if (items.length === 0 && phases.length > 0) {
    for (const phase of phases) {
      items.push({ type: "phase", phase });
    }
  }

  // Add currently streaming thinking block
  if (activeThinkingId && thinkingBuffer) {
    items.push({ type: "streaming_thinking" });
  }

  // Add streaming text at the end
  if (streamingText) {
    items.push({ type: "text" });
  }

  return (
    <>
      {items.map((item) => {
        switch (item.type) {
          case "phase":
            return (
              <div key={`phase-${item.phase}`} style={styles.phaseDivider}>
                <div style={styles.phaseLine} />
                <span style={styles.phasePill}>{item.phase}</span>
                <div style={styles.phaseLine} />
              </div>
            );
          case "thinking":
            return (
              <ThinkingBlock
                key={`think-${item.block.id}`}
                block={item.block}
                isStreaming={false}
                streamingContent=""
              />
            );
          case "tool":
            return <ToolCallCard key={`tool-${item.entry.tool_call_id}`} entry={item.entry} />;
          case "streaming_thinking":
            return (
              <ThinkingBlock
                key="streaming-thinking"
                block={null}
                isStreaming={true}
                streamingContent={thinkingBuffer}
              />
            );
          case "text":
            return (
              <StreamingText
                key="streaming-text"
                text={streamingText}
                isStreaming={isRunning}
              />
            );
          default:
            return null;
        }
      })}
    </>
  );
}

// --- Raw Log View ---

type DisplayRow =
  | { kind: "event"; event: SSEEvent }
  | { kind: "coalesced"; label: string; count: number; text: string };

function RawLogView({ events }: { events: SSEEvent[] }) {
  // Drop token_update noise, then coalesce consecutive text_delta / thinking_delta
  // events into single rows so the log reads as sentences instead of characters.
  const rows: DisplayRow[] = [];
  for (const event of events) {
    if (event.event === "token_update") continue;
    if (event.event === "text_delta" || event.event === "thinking_delta") {
      const last = rows[rows.length - 1];
      const content = (event.data as { content?: string }).content ?? "";
      if (
        last &&
        last.kind === "coalesced" &&
        last.label === event.event
      ) {
        last.text += content;
        last.count += 1;
        continue;
      }
      rows.push({
        kind: "coalesced",
        label: event.event,
        count: 1,
        text: content,
      });
      continue;
    }
    rows.push({ kind: "event", event });
  }

  return (
    <>
      {rows.map((row, i) =>
        row.kind === "event" ? (
          <div key={i} style={styles.rawEvent}>
            <span
              style={{
                ...styles.rawBadge,
                ...(RAW_BADGE_COLORS[row.event.event] || {}),
              }}
            >
              {row.event.event}
            </span>
            <span style={styles.rawContent}>{formatRawEvent(row.event)}</span>
          </div>
        ) : (
          <div key={i} style={styles.rawEvent}>
            <span
              style={{
                ...styles.rawBadge,
                ...(RAW_BADGE_COLORS[row.label] || {}),
              }}
            >
              {row.label} ×{row.count}
            </span>
            <span style={styles.rawContent}>{truncate(row.text, 160)}</span>
          </div>
        ),
      )}
    </>
  );
}

function truncate(s: string, max: number): string {
  const collapsed = s.replace(/\s+/g, " ").trim();
  return collapsed.length > max ? collapsed.slice(0, max - 1) + "…" : collapsed;
}

function formatRawEvent(event: SSEEvent): string {
  switch (event.event) {
    case "status":
      return `${(event.data as StatusData).phase} — ${(event.data as StatusData).message}`;
    case "tool_call": {
      const d = event.data as ToolCallData;
      const args = JSON.stringify(d.args);
      return `${d.tool_name}(${args.length > 80 ? args.slice(0, 79) + "…" : args})`;
    }
    case "tool_result":
      return `${(event.data as ToolResultData).tool_name} → ${truncate((event.data as ToolResultData).result_summary, 140)}`;
    case "thinking_end":
      return `summary: ${truncate((event.data as { summary?: string }).summary ?? "", 140)}`;
    default:
      return truncate(JSON.stringify(event.data), 140);
  }
}
