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
function findTerminalEvent(events: SSEEvent[]): TerminalEvent | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event === "complete" || e.event === "run_complete" || e.event === "error") {
      return e;
    }
  }
  return null;
}

// The only three events findTerminalEvent returns. Narrowing the prop type
// lets TS resolve `event.data` to the right payload in each branch instead of
// the full SSEEvent union.
type TerminalEvent = Extract<SSEEvent, { event: "complete" | "run_complete" | "error" }>;

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
    borderColor: pwc.errorBorder,
    background: "#FFF8F8",
  } as React.CSSProperties,
  terminalDoneBadge: {
    background: pwc.successBg,
    color: pwc.successText,
  } as React.CSSProperties,
  terminalErrorBadge: {
    background: pwc.errorBg,
    color: pwc.errorText,
  } as React.CSSProperties,
  // Partial-success styling — greenish frame (still "completed") with a
  // tinted amber badge so the row reads as "done but read this". Borrowed
  // from the standard warningBg/warningText tokens so dark-mode stays
  // consistent with the rest of the UI.
  terminalWarnBadge: {
    background: pwc.warningBg,
    color: pwc.warningText,
  } as React.CSSProperties,
  warningsBlock: {
    marginTop: pwc.space.xs,
    padding: "8px 10px",
    borderRadius: pwc.radius.sm,
    border: `1px solid ${pwc.warningBorder}`,
    background: pwc.warningBg,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey900,
  } as React.CSSProperties,
  warningsTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.warningText,
    marginBottom: 4,
  } as React.CSSProperties,
  warningsList: {
    margin: 0,
    paddingLeft: 16,
  } as React.CSSProperties,
};

function TerminalRow({ event }: { event: TerminalEvent }) {
  // Discriminated on event.event:
  //   complete:     { success, error?, warnings?, ... }  (CompleteData | AgentCompleteData)
  //   run_complete: { success, merge_errors, ... }       (RunCompleteData)
  //   error:        { message, ... }                     (ErrorData)
  if (event.event === "complete" || event.event === "run_complete") {
    const data = event.data;
    const success = data.success === true;
    if (success) {
      // Non-fatal diagnostics from notes agents (writer skips, borderline
      // fuzzy matches, partial sub-agent coverage). Present on CompleteData
      // and AgentCompleteData for notes; face-statement complete events
      // don't carry the field. Peer-review finding #3: without surfacing
      // these, a partial-success notes run looks fully green.
      const warnings: string[] | undefined =
        event.event === "complete" ? (data as { warnings?: string[] }).warnings : undefined;
      const hasWarnings = Array.isArray(warnings) && warnings.length > 0;
      // Wrap in a column so the warning block can sit *below* the row
      // (rather than inside the flex-aligned row) while still being part
      // of the same terminal block visually.
      return (
        <div data-terminal={hasWarnings ? "done-with-warnings" : "done"}>
          <div style={{ ...styles.terminalRow, ...styles.terminalDone }}>
            <div style={styles.terminalMain}>
              <span
                aria-hidden="true"
                style={{ ...styles.terminalDot, background: pwc.success, boxShadow: `0 0 0 3px ${pwc.successBg}` }}
              />
              <span style={styles.terminalLabel}>Run finished</span>
            </div>
            <span
              style={{
                ...styles.terminalBadge,
                ...(hasWarnings ? styles.terminalWarnBadge : styles.terminalDoneBadge),
              }}
            >
              {hasWarnings ? `Completed · ${warnings!.length} warning${warnings!.length === 1 ? "" : "s"}` : "Completed"}
            </span>
          </div>
          {hasWarnings && (
            <div role="note" aria-label="Run warnings" style={styles.warningsBlock}>
              <div style={styles.warningsTitle}>Warnings</div>
              <ul style={styles.warningsList}>
                {warnings!.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      );
    }
    // Failed path. Three possible reason sources depending on which
    // backend branch emitted the event:
    //   - AgentCompleteData: `error` (per-agent failure)
    //   - RunCompleteData validation-fail: `message` (pre-run rejection)
    //   - RunCompleteData merge-fail: `merge_errors[0]` (post-run rollup)
    // `message` is checked before `merge_errors` so a validation rejection's
    // actionable reason ("Model setup failed: …") wins over the generic
    // rollup label.
    let err: string | null = null;
    if ("error" in data && typeof data.error === "string") {
      err = data.error;
    } else if ("message" in data && typeof data.message === "string" && data.message) {
      err = data.message;
    } else if ("merge_errors" in data && Array.isArray(data.merge_errors) && data.merge_errors.length > 0) {
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
            style={{ ...styles.terminalDot, background: pwc.error, boxShadow: `0 0 0 3px ${pwc.errorBg}` }}
          />
          <span style={styles.terminalLabel}>{err ?? "Failed"}</span>
        </div>
        <span style={{ ...styles.terminalBadge, ...styles.terminalErrorBadge }}>
          Failed
        </span>
      </div>
    );
  }
  // event: "error" — ErrorData carries a typed `message`.
  const msg = event.data.message || "Error";
  return (
    <div
      data-terminal="error"
      style={{ ...styles.terminalRow, ...styles.terminalError }}
    >
      <div style={styles.terminalMain}>
        <span
          aria-hidden="true"
          style={{ ...styles.terminalDot, background: pwc.error, boxShadow: `0 0 0 3px ${pwc.errorBg}` }}
        />
        <span style={styles.terminalLabel}>{msg}</span>
      </div>
      <span style={{ ...styles.terminalBadge, ...styles.terminalErrorBadge }}>
        Failed
      </span>
    </div>
  );
}

export function AgentTimeline({ events, toolTimeline, isRunning: _isRunning }: Props) {
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
