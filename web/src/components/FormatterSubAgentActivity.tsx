import { useMemo, useState, type ReactNode } from "react";
import type { AgentTabStatus, SSEEvent } from "../lib/types";
import { buildToolTimeline } from "../lib/buildToolTimeline";
import { deriveFormatterSheets, labelFormatterEvents } from "../lib/formatterSubAgents";
import { pwc } from "../lib/theme";
import { ActivityStream } from "./ActivityStream";
import { NotesSubTabBar } from "./NotesSubTabBar";
import { AgentTimeline } from "./AgentTimeline";
import { deriveFormatterSubAgents, formatterSubAgentLabel } from "../lib/formatterSubAgents";

/** Notes-formatting activity with one chip per notes sheet, laid out like
 *  every other workstream: chips, a failure note when one applies, then the
 *  activity stream. A large sheet's parts share its chip; "All" shows each
 *  part's named status lines. Shared by the live tab and History. */
export function FormatterSubAgentActivity({ events, status, recorded = false, technical }: {
  events: SSEEvent[];
  status: AgentTabStatus;
  recorded?: boolean;
  /** Shown in place of the stream when Technical detail is ticked. */
  technical?: (sheet: string | null) => ReactNode;
}) {
  const [sheetKey, setSheetKey] = useState<string | null>(null);
  const sheets = useMemo(() => deriveFormatterSheets(events), [events]);
  const selected = sheets.find((sheet) => sheet.sheet === sheetKey) ?? null;
  const scopedEvents = useMemo(() => {
    const ids = new Set(selected?.subAgentIds ?? []);
    return selected
      ? events.filter((event) => ids.has(String((event.data as unknown as Record<string, unknown>).sub_agent_id ?? "")))
      : events;
  }, [events, selected]);
  const stream = useMemo(() => {
    const shown = selected ? scopedEvents : scopedEvents.filter((event) => event.event === "status");
    // Part names only help when a sheet was split; a single pass reads plainly.
    const labelled = !selected || selected.subAgentIds.length > 1 ? labelFormatterEvents(shown) : shown;
    return { events: labelled, toolTimeline: buildToolTimeline(labelled) };
  }, [scopedEvents, selected]);
  if (sheets.length === 0) return null;
  const shownStatus = selected?.status ?? status;
  // A failure is reported once the sheet (or, on "All", the whole pass) ends.
  const failed = (selected ? [selected] : status === "running" ? [] : sheets)
    .filter((sheet) => sheet.status === "failed");
  return <>
    <NotesSubTabBar
      ariaLabel="Formatting by sheet"
      showStatus
      failureLabel="Needs review"
      subAgents={sheets.map((sheet) => ({
        subAgentId: sheet.sheet,
        notes: null,
        pages: null,
        status: sheet.status,
        label: sheet.partsTotal > 1 && sheet.status === "running"
          ? `${sheet.label} · ${sheet.partsDone} of ${sheet.partsTotal}` : sheet.label,
      }))}
      activeSubId={selected?.sheet ?? null}
      onSelect={setSheetKey}
    />
    {failed.length > 0 && (
      <div data-testid="formatting-failure" style={styles.failure}>
        <span style={styles.failureLabel}>Needs attention</span>
        {failed.map((sheet) => (
          <span key={sheet.sheet}>
            {!selected && <strong style={styles.sheetName}>{sheet.label}: </strong>}
            {sheet.failures[0] ?? "Formatting did not finish. Retry it from the Notes tab."}
          </span>
        ))}
      </div>
    )}
    <ActivityStream
      events={stream.events}
      toolTimeline={stream.toolTimeline}
      reasoningBlocks={[]}
      isRunning={shownStatus === "running" || shownStatus === "pending"}
      status={shownStatus}
      recorded={recorded}
      streamKey={`notes-formatting:${selected?.sheet ?? "all"}`}
      technical={<>
        {technical?.(selected?.sheet ?? null)}
        <FormatterTechnicalActivity events={scopedEvents} />
      </>}
    />
  </>;
}

/** Separate timelines keep parallel parts' tool calls and phases distinct. */
function FormatterTechnicalActivity({ events }: { events: SSEEvent[] }) {
  return <div>
    <p style={styles.execution}>Parts run in parallel; checks and corrections run in sequence within each part.</p>
    {deriveFormatterSubAgents(events).map((part) => {
      const steps = events.filter((event) => event.data.sub_agent_id === part.subAgentId);
      return <section key={part.subAgentId} aria-label={formatterSubAgentLabel(part.subAgentId)}>
        <h3 style={styles.partHeading}>{part.label}</h3>
        {steps.map((event, index) => event.event === "status" && event.data.phase === "failed" && event.data.detail
          ? <div key={index} style={styles.detail}>{String(event.data.detail)}</div> : null)}
        <AgentTimeline events={steps} toolTimeline={buildToolTimeline(steps)} reasoningBlocks={[]}
          isRunning={part.status === "running" || part.status === "pending"} showStatusSteps />
      </section>;
    })}
  </div>;
}

// Same "What went wrong" treatment as a failed workstream in RunDetailView.
const styles = {
  execution: { fontSize: 13, color: pwc.grey700, padding: "0 16px" },
  partHeading: { fontSize: 14, fontWeight: pwc.weight.medium, padding: "12px 16px 0" },
  detail: { padding: "8px 16px", whiteSpace: "pre-wrap" as const, overflowWrap: "anywhere" as const },
  failure: {
    display: "grid",
    gap: 4,
    padding: "10px 0",
    color: pwc.grey900,
    borderTop: `1px solid ${pwc.grey100}`,
    fontSize: 14,
    lineHeight: 1.55,
  },
  failureLabel: { fontSize: 13, fontWeight: pwc.weight.medium, color: pwc.grey700 },
  sheetName: { fontWeight: pwc.weight.medium },
};
