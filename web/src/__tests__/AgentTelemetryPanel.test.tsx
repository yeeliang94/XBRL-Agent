import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { AgentTelemetryPanel } from "../components/AgentTelemetryPanel";
import { pwc } from "../lib/theme";
import type { RunDetailJson } from "../lib/types";


function detail(): RunDetailJson {
  return {
    id: 42,
    created_at: "2026-08-28T00:00:00Z",
    pdf_filename: "failed.pdf",
    status: "failed",
    session_id: "session-42",
    output_dir: "/tmp/session-42",
    merged_workbook_path: null,
    scout_enabled: false,
    started_at: "2026-08-28T00:00:00Z",
    ended_at: "2026-08-28T00:01:00Z",
    config: null,
    agents: [],
    cross_checks: [],
    incidents: [{
      id: 1,
      created_at: "2026-08-28T00:00:01Z",
      source: "run_validation",
      stage: "validation",
      severity: "fatal",
      error_code: "model_setup_failed",
      user_message: "The selected model could not be started.",
      technical_message: "ProviderConnectionError: refused",
      exception_type: "ProviderConnectionError",
      correlation_id: "request-123",
      details: null,
    }],
    run_events: [{
      event: "pipeline_stage",
      data: { stage: "extracting", started_at: 1 },
      timestamp: 1,
    }],
  };
}


describe("AgentTelemetryPanel incidents", () => {
  test("shows a safe explanation, next action, and support reference without agents", () => {
    render(<AgentTelemetryPanel detail={detail()} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The selected model could not be started.",
    );
    expect(screen.getByText(/Check model credentials and connectivity/)).toBeInTheDocument();
    expect(screen.getByText(/request-123/)).toBeInTheDocument();
    expect(screen.getByText("No agents were recorded for this run.")).toBeInTheDocument();
    expect(screen.getByText(/Coordinator timeline \(1 event\)/)).toBeInTheDocument();
  });

  test("renders recoverable incidents as advisories rather than fatal errors", () => {
    const advisory = detail();
    advisory.incidents![0] = {
      ...advisory.incidents![0],
      severity: "recoverable",
      user_message: "The document scan could not finish.",
    };
    render(<AgentTelemetryPanel detail={advisory} />);

    const card = screen.getByText("The document scan could not finish.").closest("article");
    expect(card).toHaveStyle({ background: pwc.warningBg });
  });
});
