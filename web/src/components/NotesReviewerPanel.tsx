import { useCallback, useEffect, useState } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import type { ModelEntry } from "../lib/types";
import { ApiError, userMessage } from "../lib/errors";
import { flagKindLabel, humanize } from "../lib/vocabulary";
import { ConfirmDialog } from "./ConfirmDialog";
import { SkeletonText } from "./Skeleton";

/**
 * Notes Reviewer panel (docs/PLAN.md — Notes Reviewer, Phase 4).
 *
 * The notes-prose analogue of ReviewTab: surfaces the notes reviewer's work for
 * a run — the original → reviewer prose diff (authored / edited / cleared), the
 * flags it raised (stuck / disputes_prior / needs_human), a one-click "Revert to
 * original", and a model + "Re-review" control. Reads the /notes-review surface;
 * mounted above the prose editor in the Notes tab. Inline styles + theme tokens
 * only (gotcha #7).
 */

interface NotesDiffRow {
  sheet: string;
  row: number;
  label: string | null;
  change: "authored" | "edited" | "cleared";
  original_html: string | null;
  current_html: string | null;
  evidence: string | null;
}

interface NotesFlagRow {
  id: number;
  kind: string;
  reason: string;
  sheet: string | null;
  row: number | null;
  status: string;
  answer: string | null;
}

interface NotesReviewPayload {
  run_id: number;
  has_reviewer_version: boolean;
  diff: NotesDiffRow[];
  flags: NotesFlagRow[];
}

interface ReReviewOutcome {
  ok?: boolean;
  error?: string;
  invoked?: boolean;
  writes_performed?: number;
  flags_raised?: number;
  model?: string;
}

interface Props {
  runId: number;
}

async function errorDetail(r: Response): Promise<string> {
  let body: unknown = null;
  try {
    body = await r.json();
  } catch { /* no JSON body */ }
  return ApiError.fromResponse(r.status, body).message;
}

/** Poll the background pass until it finishes (it reads the PDF, can take minutes). */
async function pollStatus(runId: number): Promise<ReReviewOutcome> {
  const MAX = 600; // ~15 min at 1.5s
  for (let i = 0; i < MAX; i++) {
    const r = await fetch(`/api/runs/${runId}/notes-review/status`);
    if (!r.ok) throw ApiError.fromResponse(r.status, null);
    const s = await r.json();
    if (s.status === "done") return s as ReReviewOutcome;
    if (s.status === "idle") return { ok: true, invoked: false };
    await new Promise((res) => setTimeout(res, 1500));
  }
  throw new Error("Re-review timed out — reopen this tab later to see results.");
}

/** Plain-text preview of a stored HTML cell (the diff shows direction, not full prose). */
function preview(html: string | null): string {
  if (!html) return "—";
  const text = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return text.length > 140 ? `${text.slice(0, 140)}…` : text || "—";
}

const CHANGE_LABEL: Record<NotesDiffRow["change"], string> = {
  authored: "Authored",
  edited: "Edited",
  cleared: "Cleared",
};

export function NotesReviewerPanel({ runId }: Props) {
  const [data, setData] = useState<NotesReviewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "review" | "revert">(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  // Collapsed by default: the notes editor is the surface the operator works
  // in, so the reviewer's diff/flags fold into a one-line summary bar that
  // expands on demand (Phase 5 de-cluttering).
  const [expanded, setExpanded] = useState(false);
  const [confirmRevert, setConfirmRevert] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (cancelled || !s) return;
        setModels(s.available_models || []);
        const dflt =
          (s.default_models && s.default_models.notes_reviewer) || s.model || "";
        setSelectedModel(dflt);
      })
      .catch(() => {
        /* model picker is best-effort */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch(`/api/runs/${runId}/notes-review`, { signal });
        if (!r.ok) throw new Error(await errorDetail(r));
        setData(await r.json());
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(userMessage(e));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [runId],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const reReview = async () => {
    setBusy("review");
    setActionError(null);
    setNotice(null);
    try {
      const r = await fetch(`/api/runs/${runId}/notes-review/re-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selectedModel || undefined }),
      });
      if (!r.ok) throw new Error(await errorDetail(r));
      const result = await pollStatus(runId);
      if (result && result.ok === false) {
        setActionError(`Re-review did not complete: ${result.error ?? "unknown error"}`);
      } else {
        const usedModel = result?.model ? ` (model: ${result.model})` : "";
        if (result?.invoked === false) {
          setNotice(`No notes findings to review${usedModel}.`);
        } else if ((result?.writes_performed ?? 0) === 0) {
          setNotice(
            `Reviewer ran but made no changes${usedModel}. Flags raised: ${result?.flags_raised ?? 0}.`,
          );
        } else {
          setNotice(
            `Reviewer applied ${result.writes_performed} change(s)${usedModel}. Flags raised: ${result?.flags_raised ?? 0}.`,
          );
        }
      }
      await load();
    } catch (e) {
      setActionError(userMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const revert = async () => {
    setBusy("revert");
    setActionError(null);
    setNotice(null);
    try {
      const r = await fetch(`/api/runs/${runId}/notes-review/revert-to-original`, {
        method: "POST",
      });
      if (!r.ok) throw new Error(await errorDetail(r));
      await load();
    } catch (e) {
      setActionError(userMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const answerFlag = async (flagId: number) => {
    const text = (answers[flagId] || "").trim();
    if (!text) return;
    setActionError(null);
    try {
      const r = await fetch(`/api/runs/${runId}/notes-flags/${flagId}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer: text }),
      });
      if (!r.ok) throw new Error(await errorDetail(r));
      await load();
    } catch (e) {
      setActionError(userMessage(e));
    }
  };

  if (loading)
    return (
      <div style={styles.panel}>
        <SkeletonText lines={2} label="Loading notes review…" />
      </div>
    );
  if (error)
    return (
      <p style={styles.error} role="alert">
        {error}
      </p>
    );
  if (!data) return null;

  // Nothing to show and no flags — keep the panel quiet (just the re-review
  // control) so it doesn't clutter the editor on a clean run.
  return (
    <div data-testid="notes-reviewer-panel" style={styles.panel}>
      {actionError && (
        <p style={styles.error} role="alert">
          {actionError}
        </p>
      )}
      {notice && (
        <p style={styles.notice} role="status" data-testid="notes-review-notice">
          {notice}
        </p>
      )}

      <div style={styles.headerRow}>
        {/* Summary-bar toggle: a one-line "N change(s), M flag(s) — view
            details" that expands the diff/flags in place. */}
        <button
          type="button"
          style={styles.summaryToggle}
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          data-testid="notes-reviewer-toggle"
        >
          <span aria-hidden="true" style={styles.chevron}>{expanded ? "▾" : "▸"}</span>
          <span style={styles.title}>Notes review</span>
          <span style={styles.dim}>
            {data.diff.length} change{data.diff.length === 1 ? "" : "s"} ·{" "}
            {data.flags.length} flag{data.flags.length === 1 ? "" : "s"}
          </span>
        </button>
        {data.has_reviewer_version ? (
          <span style={ui.status} data-testid="notes-reviewer-version-indicator">
            <span aria-hidden="true" style={ui.statusSymbol}>{STATUS_SYMBOLS.derived}</span>
            Reviewer version
          </span>
        ) : (
          <span style={styles.dim} data-testid="no-notes-reviewer-version">
            No reviewer changes yet.
          </span>
        )}
        <div style={styles.headerSpacer} />
        <label style={styles.modelLabel}>
          <span style={styles.srOnly}>Notes reviewer model</span>
          <select
            style={styles.modelSelect}
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            aria-label="Notes reviewer model"
          >
            {selectedModel && !models.some((m) => m.id === selectedModel) && (
              <option value={selectedModel}>{selectedModel}</option>
            )}
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name || m.id}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          style={styles.reviewBtn}
          onClick={reReview}
          disabled={busy !== null}
        >
          {busy === "review" ? "Reviewing…" : "Run notes review again"}
        </button>
        {data.has_reviewer_version && (
          <button
            type="button"
            style={styles.revertBtn}
            onClick={() => setConfirmRevert(true)}
            disabled={busy !== null}
          >
            {busy === "revert" ? "Restoring…" : "Restore original extraction"}
          </button>
        )}
      </div>

      <ConfirmDialog
        isOpen={confirmRevert}
        title="Restore the original notes?"
        message="The notes review's changes to the prose will be discarded and the notes go back to what was first extracted from the PDF. Your own manual edits to those cells are also affected."
        confirmLabel="Restore original"
        busyLabel="Restoring…"
        busy={busy === "revert"}
        onConfirm={() => {
          setConfirmRevert(false);
          void revert();
        }}
        onCancel={() => setConfirmRevert(false)}
      />

      {busy === "review" && (
        <p style={styles.dim} role="status">
          The reviewer reads the PDF and fixes each finding — this can take a few
          minutes. Leave this tab open; results appear when it finishes.
        </p>
      )}

      {expanded && (data.diff.length > 0 || data.flags.length > 0) && (
        <>
          <h4 style={styles.h4}>Changes ({data.diff.length})</h4>
          {data.diff.length === 0 ? (
            <p style={styles.dim}>No prose changes.</p>
          ) : (
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Cell</th>
                  <th style={styles.th}>Change</th>
                  <th style={styles.th}>Original → Reviewer</th>
                </tr>
              </thead>
              <tbody>
                {data.diff.map((d) => (
                  <tr key={`${d.sheet}:${d.row}`} data-testid={`notes-diff-${d.sheet}-${d.row}`}>
                    <td style={styles.td}>
                      <div style={styles.cellLabel}>{d.label ?? `Row ${d.row}`}</div>
                      <div style={styles.dim}>
                        {d.sheet} row {d.row}
                      </div>
                    </td>
                    <td style={styles.td}>
                      <span style={styles.changeChip}>{CHANGE_LABEL[d.change]}</span>
                    </td>
                    <td style={styles.td}>
                      <div style={styles.oldVal}>{preview(d.original_html)}</div>
                      <div style={styles.newVal}>{preview(d.current_html)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h4 style={styles.h4}>Flags ({data.flags.length})</h4>
          {data.flags.length === 0 ? (
            <p style={styles.dim}>No flags.</p>
          ) : (
            <div style={styles.flagStack}>
              {data.flags.map((f) => (
                <div key={f.id} style={styles.flagCard} data-testid={`notes-flag-${f.id}`}>
                  <div style={styles.flagHead}>
                    <span style={styles.kindChip}>
                      <span aria-hidden="true" style={ui.badgeDot(pwc.warning)} />
                      {flagKindLabel(f.kind)}
                    </span>
                    <span style={styles.dim}>{humanize(f.status)}</span>
                    {f.sheet && f.row != null && (
                      <span style={styles.dim}>
                        {f.sheet} row {f.row}
                      </span>
                    )}
                  </div>
                  <p style={styles.flagReason}>{f.reason}</p>
                  {f.answer ? (
                    <p style={styles.answerGiven}>Your answer: {f.answer}</p>
                  ) : (
                    <div style={styles.answerRow}>
                      <textarea
                        style={styles.answerBox}
                        placeholder="Answer / guidance for this flag…"
                        value={answers[f.id] || ""}
                        onChange={(e) =>
                          setAnswers((a) => ({ ...a, [f.id]: e.target.value }))
                        }
                        aria-label={`Answer notes flag ${f.id}`}
                      />
                      <button
                        type="button"
                        style={styles.smallBtn}
                        onClick={() => answerFlag(f.id)}
                        disabled={!(answers[f.id] || "").trim()}
                      >
                        Save
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const styles = {
  panel: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    padding: pwc.space.md,
    marginBottom: pwc.space.lg,
    background: pwc.grey50,
  } as const,
  dim: { color: pwc.grey500, fontSize: 13 },
  error: { color: pwc.errorText, fontSize: 13 },
  notice: {
    ...ui.alertInfo,
    padding: pwc.space.sm,
    fontSize: 13,
    margin: `0 0 ${pwc.space.md}px`,
  } as const,
  headerRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
  } as const,
  headerSpacer: { flex: 1 },
  summaryToggle: {
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.sm,
    background: "transparent",
    border: "none",
    padding: 0,
    cursor: "pointer",
    textAlign: "left" as const,
  } as const,
  chevron: { color: pwc.grey500, fontSize: 12, width: 12, display: "inline-block" } as const,
  title: { fontFamily: pwc.fontHeading, fontWeight: 600, color: pwc.grey900, fontSize: 14 },
  badge: {
    ...ui.badge,
    borderColor: pwc.info,
  } as const,
  srOnly: {
    position: "absolute" as const,
    width: 1,
    height: 1,
    overflow: "hidden",
    clip: "rect(0 0 0 0)",
  } as const,
  modelLabel: { display: "flex", alignItems: "center" } as const,
  modelSelect: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    minWidth: 180,
  } as const,
  h4: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
    margin: `${pwc.space.md}px 0 ${pwc.space.sm}px`,
  } as const,
  table: { width: "100%", borderCollapse: "collapse" as const, fontSize: 13 } as const,
  th: {
    textAlign: "left" as const,
    padding: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey700,
    fontWeight: 600,
  } as const,
  td: {
    padding: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey100}`,
    verticalAlign: "top" as const,
    color: pwc.grey800,
  } as const,
  cellLabel: { fontWeight: 600, color: pwc.grey900 },
  oldVal: { color: pwc.grey500, textDecoration: "line-through" },
  newVal: { color: pwc.successText, fontWeight: 600, marginTop: 2 },
  changeChip: {
    background: pwc.grey100,
    border: `1px solid ${pwc.grey200}`,
    color: pwc.grey700,
    borderRadius: pwc.radius.pill,
    padding: `1px ${pwc.space.sm}px`,
    fontSize: 11,
    fontWeight: 600,
  } as const,
  flagStack: { display: "flex", flexDirection: "column" as const, gap: pwc.space.sm },
  flagCard: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    padding: pwc.space.md,
    background: pwc.white,
  } as const,
  flagHead: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    marginBottom: pwc.space.xs,
  } as const,
  kindChip: {
    ...ui.badge,
    borderColor: pwc.warning,
  } as const,
  flagReason: { color: pwc.grey800, fontSize: 13, margin: `${pwc.space.xs}px 0` },
  answerGiven: { color: pwc.successText, fontSize: 13, margin: 0 },
  answerRow: { display: "flex", gap: pwc.space.sm, alignItems: "flex-start" },
  answerBox: {
    flex: 1,
    minHeight: 44,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    padding: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as const,
  // Neutral secondary, not a blue status-colour fill — one primary (orange)
  // action per screen (docs/PLAN-design-qa-fixes.md C1).
  reviewBtn: {
    ...ui.buttonSecondary,
    ...ui.buttonSm,
  } as const,
  revertBtn: {
    background: pwc.white,
    color: pwc.errorText,
    border: `1px solid ${pwc.errorBorder}`,
    borderRadius: pwc.radius.md,
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontWeight: 600,
    cursor: "pointer",
  } as const,
  smallBtn: {
    ...ui.buttonSecondary,
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontSize: 13,
  } as const,
};
