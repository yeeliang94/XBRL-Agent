import React from "react";
import { pwc } from "./../lib/theme";

// Nested sub-tab bar for the Notes-12 content pane. Sheet 12 fans out to
// 5 parallel sub-agents; this bar lets operators filter the timeline to
// one sub-agent at a time without adding 5 top-level tabs to AgentTabs.
//
// Kept stateless on purpose — `activeSubId` + `onSelect` live on the
// parent (ExtractPage / RunDetailView) so the selection survives across
// rerenders triggered by incoming SSE events.

export interface NotesSubAgentRange {
  subAgentId: string;            // stable id, e.g. "notes:LIST_OF_NOTES:sub0"
  notes: [number, number];       // inclusive note-number range
  pages: [number, number];       // inclusive PDF page range
}

export interface NotesSubTabBarProps {
  subAgents: NotesSubAgentRange[];
  /** null == the "All" view (coordinator events + every sub-agent) */
  activeSubId: string | null;
  onSelect: (subId: string | null) => void;
}

export function NotesSubTabBar({
  subAgents,
  activeSubId,
  onSelect,
}: NotesSubTabBarProps) {
  // Don't render an orphan "All" chip when there are no sub-agents yet —
  // the parent relies on `subAgents.length > 0` to gate this component,
  // but we also defensively bail here so the component has no hidden
  // dependency on that gating.
  if (subAgents.length === 0) return null;

  return (
    <div role="tablist" aria-label="Sheet-12 sub-agents" style={styles.bar}>
      <button
        role="tab"
        type="button"
        aria-selected={activeSubId === null}
        onClick={() => onSelect(null)}
        style={chipStyle(activeSubId === null)}
      >
        All
      </button>
      {subAgents.map((s, idx) => {
        const active = s.subAgentId === activeSubId;
        // Short chip label: 1-indexed display number + terse note range.
        // Full page span is exposed via title/tooltip so the chip stays
        // narrow enough to fit 5 across without wrapping.
        const labelNum = idx + 1;
        const noteRange = s.notes[0] === s.notes[1]
          ? `Note ${s.notes[0]}`
          : `Notes ${s.notes[0]}-${s.notes[1]}`;
        const pageRange = s.pages[0] === s.pages[1]
          ? `p ${s.pages[0]}`
          : `pp ${s.pages[0]}-${s.pages[1]}`;
        return (
          <button
            key={s.subAgentId}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onSelect(s.subAgentId)}
            title={`${noteRange}, ${pageRange}`}
            style={chipStyle(active)}
          >
            Sub {labelNum} · {noteRange}
          </button>
        );
      })}
    </div>
  );
}

// Visual style: smaller/secondary compared to AgentTabs (lighter bg, no
// abort/rerun controls). Reuses the same pill shape so the sub-bar reads
// as a refinement of the active tab rather than a separate widget.
function chipStyle(active: boolean): React.CSSProperties {
  return {
    padding: "4px 10px",
    fontFamily: pwc.fontBody,
    fontSize: 12,
    fontWeight: active ? 600 : 500,
    color: active ? pwc.orange700 : pwc.grey700,
    background: active ? pwc.orange50 : pwc.white,
    border: `1px solid ${active ? pwc.orange400 : pwc.grey200}`,
    borderRadius: 999,
    cursor: "pointer",
    whiteSpace: "nowrap",
    transition: "color 0.15s, background 0.15s, border-color 0.15s",
  };
}

const styles = {
  bar: {
    display: "flex",
    gap: 6,
    alignItems: "center",
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    background: pwc.grey50,
    overflowX: "auto" as const,
  } as React.CSSProperties,
};
