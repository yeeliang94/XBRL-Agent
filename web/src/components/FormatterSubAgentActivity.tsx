import { useMemo, useState } from "react";
import type { AgentTabStatus, SSEEvent } from "../lib/types";
import { buildToolTimeline, filterEventsBySubAgent } from "../lib/buildToolTimeline";
import { deriveFormatterSubAgents, labelFormatterEvents } from "../lib/formatterSubAgents";
import { ActivityStream } from "./ActivityStream";
import { NotesSubTabBar } from "./NotesSubTabBar";

/** Notes-formatting activity split by sub-agent (one per sheet or table
 *  group), like the Sheet-12 notes sub-agents. "All" names each sub-agent
 *  on its own status lines. Shared by the live tab and History. */
export function FormatterSubAgentActivity({ events, status, recorded = false }: {
  events: SSEEvent[];
  status: AgentTabStatus;
  recorded?: boolean;
}) {
  const [subId, setSubId] = useState<string | null>(null);
  const subAgents = useMemo(() => deriveFormatterSubAgents(events), [events]);
  const selected = subAgents.find((sub) => sub.subAgentId === subId) ?? null;
  const stream = useMemo(() => {
    const shown = selected ? filterEventsBySubAgent(events, selected.subAgentId) : labelFormatterEvents(events);
    return { events: shown, toolTimeline: buildToolTimeline(shown) };
  }, [events, selected]);
  if (subAgents.length === 0) return null;
  const shownStatus = selected?.status ?? status;
  return <>
    <NotesSubTabBar
      ariaLabel="Formatting sub-agents"
      subAgents={subAgents.map((sub) => ({ ...sub, notes: null, pages: null }))}
      activeSubId={selected?.subAgentId ?? null}
      onSelect={setSubId}
    />
    <ActivityStream
      events={stream.events}
      toolTimeline={stream.toolTimeline}
      reasoningBlocks={[]}
      isRunning={shownStatus === "running"}
      status={shownStatus}
      recorded={recorded}
      streamKey={`notes-formatting:${selected?.subAgentId ?? "all"}`}
    />
  </>;
}
