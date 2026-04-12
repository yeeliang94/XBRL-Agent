import { useCallback, useEffect, useRef } from "react";
import type { SSEEvent, ToolTimelineEntry } from "../lib/types";
import { ToolCallCard } from "./ToolCallCard";
import { pwc } from "../lib/theme";

// AgentTimeline is the single replacement for ChatFeed. It renders one row
// per tool call via ToolCallCard, plus a terminal row for the final
// complete/error event. Same component is used for live extract, scout
// pre-run, and history replay so all three views look identical.

interface Props {
  events: SSEEvent[];
  toolTimeline: ToolTimelineEntry[];
  isRunning: boolean;
}

// Pluck the most recent terminal event so the row can summarise the run.
// We scan from the tail because terminal events are always near the end of
// the stream. Three event types can terminate:
//   - complete:      per-agent done
//   - run_complete:  aggregate multi-agent done (global fallback view)
//   - error:         any agent errored
//
// run_complete lives near the tail AFTER all per-agent complete events, so
// for the global/fallback path (which gets state.events) we want to prefer
// it over the last per-agent complete. For tab views fed by activeAgent.events,
// run_complete never appears so the scan falls through to complete.
function findTerminalEvent(events: SSEEvent[]): SSEEvent | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event === "complete" || e.event === "run_complete" || e.event === "error") {
      return e;
    }
  }
  return null;
}

const styles = {
  scrollArea: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
    padding: pwc.space.md,
    overflowY: "auto" as const,
    maxHeight: 500,
  } as React.CSSProperties,
  empty: {
    padding: pwc.space.md,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey500,
    textAlign: "center" as const,
  } as React.CSSProperties,
  terminalRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
    padding: "10px 12px",
    marginTop: pwc.space.xs,
    borderRadius: pwc.radius.sm,
    border: `1px solid ${pwc.grey200}`,
    background: pwc.white,
  } as React.CSSProperties,
  terminalMain: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    minWidth: 0,
  } as React.CSSProperties,
  terminalDot: {
    width: 12,
    height: 12,
    borderRadius: "50%",
    display: "inline-block",
    flexShrink: 0,
  } as React.CSSProperties,
  terminalLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
  } as React.CSSProperties,
  terminalBadge: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    padding: "2px 8px",
    borderRadius: pwc.radius.sm,
    flexShrink: 0,
  } as React.CSSProperties,
  terminalDone: {
    borderColor: "#D7F5E2",
    background: "#FCFFFD",
  } as React.CSSProperties,
  terminalError: {
    borderColor: "#FECACA",
    background: "#FFF8F8",
  } as React.CSSProperties,
  terminalDoneBadge: {
    background: "#F0FDF4",
    color: "#166534",
  } as React.CSSProperties,
  terminalErrorBadge: {
    background: "#FEF2F2",
    color: "#991B1B",
  } as React.CSSProperties,
};

function TerminalRow({ event }: { event: SSEEvent }) {
  // Shape contract (post Phase 6.5 + Phase 7 normalization):
  //   complete:     { success: bool, error?: string, ... }
  //   run_complete: { success: bool, merge_errors: string[], ... }
  //   error:        { message: string, ... }
  //
  // complete and run_complete share the `success` pass/fail signal; error
  // uses a separate shape with `message`. Cast through unknown to a record
  // since the SSE data union is discriminated by event type.
  const data = (event.data ?? {}) as unknown as Record<string, unknown>;
  if (event.event === "complete" || event.event === "run_complete") {
    const success = data.success === true;
    if (success) {
      return (
        <div
          data-terminal="done"
          style={{ ...styles.terminalRow, ...styles.terminalDone }}
        >
          <div style={styles.terminalMain}>
            <span
              aria-hidden="true"
              style={{ ...styles.terminalDot, background: pwc.success, boxShadow: "0 0 0 3px #F0FDF4" }}
            />
            <span style={styles.terminalLabel}>Run finished</span>
          </div>
          <span style={{ ...styles.terminalBadge, ...styles.terminalDoneBadge }}>
            Completed
          </span>
        </div>
      );
    }
    // Failed path — prefer explicit `error`, fall back to merge_errors[0]
    // (run_complete carries a list), then a generic label.
    let err: string | null = null;
    if (typeof data.error === "string") {
      err = data.error;
    } else if (Array.isArray(data.merge_errors) && data.merge_errors.length > 0) {
      err = String(data.merge_errors[0]);
    }
    return (
      <div
        data-terminal="error"
        style={{ ...styles.terminalRow, ...styles.terminalError }}
      >
        <div style={styles.terminalMain}>
          <span
            aria-hidden="true"
            style={{ ...styles.terminalDot, background: pwc.error, boxShadow: "0 0 0 3px #FEF2F2" }}
          />
          <span style={styles.terminalLabel}>{err ?? "Failed"}</span>
        </div>
        <span style={{ ...styles.terminalBadge, ...styles.terminalErrorBadge }}>
          Failed
        </span>
      </div>
    );
  }
  // event: "error"
  const msg = typeof data.message === "string" ? data.message : "Error";
  return (
    <div
      data-terminal="error"
      style={{ ...styles.terminalRow, ...styles.terminalError }}
    >
      <div style={styles.terminalMain}>
        <span
          aria-hidden="true"
          style={{ ...styles.terminalDot, background: pwc.error, boxShadow: "0 0 0 3px #FEF2F2" }}
        />
        <span style={styles.terminalLabel}>{msg}</span>
      </div>
      <span style={{ ...styles.terminalBadge, ...styles.terminalErrorBadge }}>
        Failed
      </span>
    </div>
  );
}

export function AgentTimeline({ events, toolTimeline, isRunning }: Props) {
  // Auto-scroll — stick to bottom unless the user has scrolled up. Same
  // pattern ChatFeed used, so users get a consistent feel after the swap.
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    userScrolledUp.current = !atBottom;
  }, []);

  const terminal = findTerminalEvent(events);

  // Scroll triggers: a new tool row OR the terminal row appearing. The
  // terminal row is its own visual element and doesn't change toolTimeline
  // length, so we depend on `terminal` (reference/presence) too — otherwise
  // a long error message can land below the fold after the final tool
  // result has already scrolled bottom into view.
  useEffect(() => {
    if (!userScrolledUp.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [toolTimeline.length, terminal]);
  const isEmpty = toolTimeline.length === 0 && terminal === null;

  // Silence unused-prop warning — isRunning is part of the contract so callers
  // can pass their existing state. Future density/animation tweaks may read it.
  void isRunning;

  if (isEmpty) {
    return (
      <div
        ref={scrollRef}
        className="agent-scroll"
        style={styles.scrollArea}
        onScroll={handleScroll}
      >
        <p style={styles.empty}>Waiting for the agent to start…</p>
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      className="agent-scroll"
      style={styles.scrollArea}
      onScroll={handleScroll}
    >
      {toolTimeline.map((entry) => (
        <ToolCallCard key={entry.tool_call_id} entry={entry} />
      ))}
      {terminal && <TerminalRow event={terminal} />}
    </div>
  );
}
