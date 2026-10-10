import { STATUS_SYMBOLS } from "../lib/runStatus";
import { HUMAN_STATUS_LABEL, type HumanSlotStatus } from "../lib/humanFile";
import { StatusIcon } from "./StatusIcon";

// ---------------------------------------------------------------------------
// HumanStatusMarker — the one icon for a human-file comparison exception,
// drawn with the shared status icon family. A different value and a value the
// AI missed need attention; an AI-only value (the human left it blank) is
// inactive. Agreement and an excluded human zero carry no marker.
// ---------------------------------------------------------------------------

const MARKED: Partial<Record<HumanSlotStatus, (typeof STATUS_SYMBOLS)[keyof typeof STATUS_SYMBOLS]>> = {
  different: STATUS_SYMBOLS.attention,
  missed: STATUS_SYMBOLS.attention,
  ai_only: STATUS_SYMBOLS.inactive,
};

export function HumanStatusMarker({ status }: { status: HumanSlotStatus | undefined }) {
  const symbol = status ? MARKED[status] : undefined;
  if (!status || !symbol) return null;
  return (
    <span role="img" aria-label={HUMAN_STATUS_LABEL[status]} title={HUMAN_STATUS_LABEL[status]}
      data-human-marker={status} style={{ display: "inline-flex", flex: "0 0 auto" }}>
      <StatusIcon symbol={symbol} />
    </span>
  );
}
