import { apiFetch } from "./api";
import type { Denomination } from "./types";

// Human-filled mTool file comparison (docs/human-mtool-file-comparison-plan.md).
// A user attaches the mTool workbook a person filled for the same document;
// the server reads it by taxonomy address and compares it with the run's
// current values on every read.

export type HumanUnit = Denomination;

export interface HumanUnmatchedRow {
  kind: "figure" | "note";
  sheet: string;
  row: number;
  label: string;
  values?: Record<string, number>;
}

export interface HumanNotCompared {
  template_id: string;
  statement: string;
  reason: "different_variant" | "not_in_file" | "not_in_run";
  human_variant?: string | null;
}

export interface HumanFileRecord {
  run_id: number;
  filename: string;
  sha256: string;
  unit: HumanUnit;
  uploaded_by: string | null;
  uploaded_at: string;
  summary: {
    typed_values?: number;
    calculated_slots?: number;
    unmatched_rows?: number;
    notes?: number;
    magnitude_warning?: string | null;
  };
  unmatched: HumanUnmatchedRow[];
  not_compared: HumanNotCompared[];
}

/** ✓ agree · ! different value · ○ missed by AI · ◇ AI-only */
export type HumanSlotStatus = "agree" | "different" | "missed" | "ai_only";

export interface HumanFigureSlot {
  concept_uuid: string;
  period: string;
  entity_scope: string;
  dimension_key: string;
  status: HumanSlotStatus;
  human_value: number | null;
  ai_value: number | null;
}

export interface HumanFigureTotals {
  human_filled: number;
  both_filled: number;
  same_value: number;
  ai_only: number;
}

export interface HumanComparison {
  file: HumanFileRecord;
  figures: {
    totals: Record<string, HumanFigureTotals>;
    slots: HumanFigureSlot[];
    excluded: { calculated: number; not_addressable: number; unmatched_rows: number };
  };
  notes: {
    totals: { human_filled: number; both_filled: number; ai_only: number };
    fields: { concept_uuid: string; status: Exclude<HumanSlotStatus, "different"> }[];
    human_html: Record<string, string>;
  };
}

export const HUMAN_STATUS_SYMBOL: Record<HumanSlotStatus, string> = {
  agree: "✓",
  different: "!",
  missed: "○",
  ai_only: "◇",
};

export const HUMAN_STATUS_LABEL: Record<HumanSlotStatus, string> = {
  agree: "Same as human",
  different: "Differs from human",
  missed: "Missed by AI",
  ai_only: "AI-only",
};

/** Lookup key for one value slot shown in the figures table. */
export function humanSlotKey(uuid: string, period: string, scope: string, dimensionKey = ""): string {
  return `${uuid}|${period}|${scope}|${dimensionKey}`;
}

export function getHumanFile(runId: number): Promise<{ file: HumanFileRecord | null }> {
  return apiFetch(`/api/runs/${runId}/human-file`);
}

export function uploadHumanFile(
  runId: number,
  file: File,
  unit: HumanUnit,
): Promise<{ file: HumanFileRecord }> {
  const form = new FormData();
  form.append("file", file);
  form.append("unit", unit);
  return apiFetch(`/api/runs/${runId}/human-file`, { method: "POST", body: form });
}

export function deleteHumanFile(runId: number): Promise<{ deleted: boolean }> {
  return apiFetch(`/api/runs/${runId}/human-file`, { method: "DELETE" });
}

export function getHumanComparison(runId: number): Promise<HumanComparison> {
  return apiFetch(`/api/runs/${runId}/human-comparison`);
}

/** A share as "45 of 50 (90%)"; "0 of 0" when nothing was filled. */
export function formatShare(part: number, whole: number): string {
  if (whole === 0) return "0 of 0";
  return `${part} of ${whole} (${Math.round((part / whole) * 100)}%)`;
}
