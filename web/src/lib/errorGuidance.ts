export interface ErrorGuidance {
  label: string;
  action: string;
}

const GUIDANCE: Record<string, ErrorGuidance> = {
  turn_timeout: {
    label: "Model response timed out",
    action: "Retry the statement. If it repeats, check provider latency and the agent trace.",
  },
  iteration_capped: {
    label: "Agent reached its turn limit",
    action: "Review the trace for repeated tool calls, then retry after correcting the source or prompt issue.",
  },
  wallclock: {
    label: "Agent ran out of time",
    action: "Retry the statement and inspect slow tools or provider calls in Activity.",
  },
  token_budget_exceeded: {
    label: "Token limit reached",
    action: "Review large or repeated context in the trace before retrying.",
  },
  projection_failed: {
    label: "Canonical projection failed",
    action: "Review the concept mapping and projection diagnostic before relying on this statement.",
  },
  save_gate_refused: {
    label: "Validation blocked the save",
    action: "Resolve the validation failures shown in Activity, then rerun the statement.",
  },
  tool_exception: {
    label: "Agent tool failed",
    action: "Open the trace and technical details to identify the failed tool.",
  },
  cancelled: {
    label: "Cancelled",
    action: "Start a new extraction if this cancellation was not intentional.",
  },
  no_write: {
    label: "No workbook data was written",
    action: "Review the trace for missing write or save calls, then retry.",
  },
  transient_exhausted: {
    label: "Provider retries were exhausted",
    action: "Check provider availability or rate limits, then retry later.",
  },
  coordinator_incident: {
    label: "Coordinator step failed",
    action: "Review the run incident and coordinator timeline in Activity.",
  },
};

export function errorGuidance(errorType: string): ErrorGuidance {
  return GUIDANCE[errorType] ?? {
    label: errorType.replace(/_/g, " "),
    action: "Review Activity and the conversation trace before retrying.",
  };
}
