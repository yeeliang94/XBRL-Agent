import { useCallback, useEffect, useState } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import type { ModelEntry } from "../lib/types";
import { ApiError, userMessage } from "../lib/errors";
import { flagKindLabel, humanize } from "../lib/vocabulary";

/**
 * Review tab (docs/Archive/PLAN-reviewer-agent.md, Step 16).
 *
 * Surfaces the reviewer pass's work for a run: the original → reviewer diff,
 * the narrow flag list (stuck / disputes_prior), a one-click "Revert to
 * original", and a guidance + "Re-review" control. Inline styles + theme
 * tokens only (gotcha #7). Lazy-mounted by RunDetailView (only when the tab
 * is active), so the fetch fires on demand.
 */

interface DiffRow {
  concept_uuid: string;
  period: string;
  entity_scope: string;
  sheet: string | null;
  row: number | null;
  col: string | null;
  label: string | null;
  original: number | null;
  current: number | null;
  reason: string | null;
  grounding: string | null;
  actor: string | null;
}

interface FlagRow {
  id: number;
  concept_uuid: string | null;
  target_sheet: string | null;
  target_row: number | null;
  category: string;
  reasoning: string | null;
  pdf_page: number | null;
  applied_fix: string | null;
  status: string;
  human_answer: string | null;
}

interface ReviewPayload {
  run_id: number;
  has_reviewer_version: boolean;
  diff: DiffRow[];
  flags: FlagRow[];
  cross_checks: unknown[];
}

interface Props {
  runId: number;
  /** Drives the parent's source-PDF pane to a diff row's cited page(s). */
  onSelectTarget?: (sheet: string, row: number) => void;
}

interface ReReviewOutcome {
  ok?: boolean;
  error?: string;
  invoked?: boolean;
  writes_performed?: number;
  flags_raised?: number;
  model?: string;
  // Item 11: set when the post-pass cascade failed — facts changed but parent
  // totals may be stale. Item 12: set when the re-export failed — DB facts
  // moved but the downloadable filled.xlsx did not (download may be stale).
  cascade_error?: string;
  export_stale?: boolean;
}

interface RevertOutcome {
  ok?: boolean;
  reverted?: boolean;
  // Item 11: false when the restore committed but the recompute raised.
  cascade_ok?: boolean;
  cascade_error?: string;
}

function fmt(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/**
 * Surface the server's `detail` on a non-2xx response instead of a bare
 * "HTTP 422". The reviewer endpoints return actionable messages (input-cap
 * 422s, the 429 concurrency cap, the 409 "nothing to revert"), so the error
 * banner should show them rather than a status code.
 */
async function errorDetail(r: Response): Promise<string> {
  let body: unknown = null;
  try {
    body = await r.json();
  } catch { /* no JSON body */ }
  return ApiError.fromResponse(r.status, body).message;
}

/**
 * Poll the background re-review pass until it finishes. A pass reads the PDF
 * and traces each failure, so it can take minutes; we poll every ~1.5s and
 * cap the wait so a stuck pass can't poll forever. The first poll fires
 * immediately (no leading delay) so a fast pass resolves without waiting.
 */
async function pollReReviewStatus(runId: number): Promise<ReReviewOutcome> {
  const MAX_POLLS = 600; // ~15 min at 1.5s
  for (let i = 0; i < MAX_POLLS; i++) {
    const r = await fetch(`/api/runs/${runId}/re-review/status`);
    if (!r.ok) throw ApiError.fromResponse(r.status, null);
    const s = await r.json();
    if (s.status === "done") return s as ReReviewOutcome;
    // "idle" right after a successful launch means the process restarted
    // mid-pass; report nothing changed rather than hanging.
    if (s.status === "idle") return { ok: true, invoked: false };
    await new Promise((res) => setTimeout(res, 1500));
  }
  throw new Error("Re-review timed out — reopen this tab later to see results.");
}

export function ReviewTab({ runId, onSelectTarget }: Props) {
  const [data, setData] = useState<ReviewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [guidance, setGuidance] = useState("");
  const [busy, setBusy] = useState<null | "review" | "revert">(null);
  // Errors from an action (re-review / revert / answer) are kept separate
  // from the initial-load `error` so a follow-up reload doesn't wipe them
  // and so they render as an inline banner instead of blanking the tab.
  const [actionError, setActionError] = useState<string | null>(null);
  // A non-error outcome message (e.g. "nothing to review", "no changes") so a
  // re-review never looks like "nothing happened" (peer-review / run #146).
  const [notice, setNotice] = useState<string | null>(null);
  // Soft warnings that aren't failures: a stale-totals cascade error (item 11)
  // or a stale-download re-export failure (item 12). The action succeeded —
  // these qualify the result, so they render as a warning banner, not an error.
  const [warning, setWarning] = useState<string | null>(null);
  // Per-flag answer-box text, keyed by flag id.
  const [answers, setAnswers] = useState<Record<number, string>>({});
  // Reviewer model picker — the same model list the rest of the app uses.
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");

  // Load the available model list + the configured reviewer default once, so
  // the user can pick which model runs a manual re-review.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (cancelled || !s) return;
        setModels(s.available_models || []);
        const dflt = (s.default_models && s.default_models.reviewer) || s.model || "";
        setSelectedModel(dflt);
      })
      .catch(() => {
        /* model picker is best-effort; re-review still works without it */
      });
    return () => { cancelled = true; };
  }, []);

  // AbortController guards against a slow /review response landing after the
  // component unmounts or after a fast runId switch — otherwise an older
  // response could overwrite a newer one's state (peer-review MEDIUM).
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/runs/${runId}/review`, { signal });
      if (!r.ok) throw new Error(await errorDetail(r));
      setData(await r.json());
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setError(userMessage(e));
    } finally {
      // Don't flip the spinner off for an aborted (superseded) request.
      if (!signal?.aborted) setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const reReview = async () => {
    setBusy("review");
    setActionError(null);
    setNotice(null);
    setWarning(null);
    try {
      const r = await fetch(`/api/runs/${runId}/re-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          guidance: guidance.trim() || undefined,
          model: selectedModel || undefined,
        }),
      });
      if (!r.ok) throw new Error(await errorDetail(r));
      // The POST only LAUNCHES the pass (it can run for minutes); poll the
      // status endpoint for the outcome. The outcome's `ok` reflects whether
      // the reviewer pass succeeded (the run is intact regardless thanks to
      // the snapshot) — surface its error instead of a phantom success
      // (peer-review HIGH).
      const result = await pollReReviewStatus(runId);
      if (result && result.ok === false) {
        setActionError(`Re-review did not complete: ${result.error ?? "unknown error"}`);
      } else {
        setGuidance("");
        // Always give explicit feedback so a re-review never looks like
        // "nothing happened" — even when there was nothing to review or the
        // reviewer made no changes (run #146 report).
        const usedModel = result?.model ? ` (model: ${result.model})` : "";
        if (result?.invoked === false) {
          setNotice(`No failing cross-checks or open conflicts to review${usedModel}.`);
        } else if ((result?.writes_performed ?? 0) === 0) {
          setNotice(`Reviewer ran but made no changes${usedModel}. ` +
            `Flags raised: ${result?.flags_raised ?? 0}.`);
        } else {
          setNotice(`Reviewer applied ${result.writes_performed} change(s)${usedModel}. ` +
            `Flags raised: ${result?.flags_raised ?? 0}.`);
        }
        // Item 11/12: the pass succeeded but may carry a soft warning — stale
        // totals (cascade failed) or a stale download (re-export failed).
        const warnings: string[] = [];
        if (result?.cascade_error) {
          warnings.push("Totals could not be recomputed after the review — re-run the review or re-check.");
        }
        if (result?.export_stale) {
          warnings.push("The downloadable workbook may be stale — the re-export failed. Re-run the review to regenerate it.");
        }
        setWarning(warnings.length ? warnings.join(" ") : null);
      }
      await load();
    } catch (e) {
      setActionError(userMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const revert = async () => {
    if (!window.confirm("Revert this run to the original extraction? The reviewer's changes will be discarded.")) {
      return;
    }
    setBusy("revert");
    setActionError(null);
    setWarning(null);
    setNotice(null);
    try {
      const r = await fetch(`/api/runs/${runId}/revert-to-original`, {
        method: "POST",
      });
      if (!r.ok) throw new Error(await errorDetail(r));
      // Item 11: the revert restored the facts (200) but the recompute may
      // have failed — surface that as a warning so the user doesn't trust
      // stale parent totals.
      const out: RevertOutcome = await r.json().catch(() => ({}));
      if (out && out.cascade_ok === false) {
        setWarning("Values were restored, but totals could not be recomputed — re-run the review or re-check.");
      }
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
      const r = await fetch(`/api/runs/${runId}/flags/${flagId}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ human_answer: text }),
      });
      if (!r.ok) throw new Error(await errorDetail(r));
      await load();
    } catch (e) {
      setActionError(userMessage(e));
    }
  };

  if (loading) return <p style={styles.dim}>Loading review…</p>;
  if (error) return <p style={styles.error} role="alert">{error}</p>;
  if (!data) return null;

  return (
    <div data-testid="review-tab">
      {actionError && (
        <p style={styles.error} role="alert">{actionError}</p>
      )}
      {notice && (
        <p style={styles.notice} role="status" data-testid="review-notice">{notice}</p>
      )}
      {warning && (
        <p style={styles.warning} role="alert" data-testid="review-warning">{warning}</p>
      )}
      {/* Reviewer-version indicator + revert */}
      <div style={styles.headerRow}>
        {data.has_reviewer_version ? (
          <span style={styles.badge} data-testid="reviewer-version-indicator">
            <span aria-hidden="true" style={ui.badgeDot(pwc.info)} />
            Reviewer version
          </span>
        ) : (
          <span style={styles.dim} data-testid="no-reviewer-version">
            No reviewer changes — this run is the original extraction.
          </span>
        )}
        {data.has_reviewer_version && (
          <button
            type="button"
            style={styles.revertBtn}
            onClick={revert}
            disabled={busy !== null}
          >
            {busy === "revert" ? "Reverting…" : "Revert to original"}
          </button>
        )}
      </div>

      {/* Diff */}
      <h3 style={styles.h3}>Changes ({data.diff.length})</h3>
      {data.diff.length === 0 ? (
        <p style={styles.dim}>No changes to review.</p>
      ) : (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Cell</th>
              <th style={styles.th}>Original → Reviewer</th>
              <th style={styles.th}>Reason</th>
              <th style={styles.th}>Grounding</th>
            </tr>
          </thead>
          <tbody>
            {data.diff.map((d) => (
              <tr key={`${d.concept_uuid}@${d.sheet}:${d.row}:${d.period}:${d.entity_scope}`}>
                <td style={styles.td}>
                  <div style={styles.cellLabel}>{d.label ?? d.concept_uuid}</div>
                  <div style={styles.dim}>
                    {d.sheet ?? "?"} row {d.row ?? "?"} · {d.period}/{d.entity_scope}
                  </div>
                </td>
                <td style={styles.td}>
                  <span style={styles.oldVal}>{fmt(d.original)}</span>
                  <span style={styles.arrow}> → </span>
                  <span style={styles.newVal}>{fmt(d.current)}</span>
                </td>
                <td style={styles.td}>{d.reason ?? "—"}</td>
                <td style={styles.td}>
                  {d.grounding ? (
                    d.sheet && d.row != null && onSelectTarget ? (
                      <button
                        type="button"
                        style={styles.linkBtn}
                        onClick={() => onSelectTarget(d.sheet as string, d.row as number)}
                      >
                        {d.grounding}
                      </button>
                    ) : (
                      d.grounding
                    )
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Flags */}
      <h3 style={styles.h3}>Flags ({data.flags.length})</h3>
      {data.flags.length === 0 ? (
        <p style={styles.dim}>No flags — the reviewer resolved everything it touched.</p>
      ) : (
        <div style={styles.flagStack}>
          {data.flags.map((f) => (
            <div key={f.id} style={styles.flagCard} data-testid={`flag-${f.id}`}>
              <div style={styles.flagHead}>
                <span
                  style={f.category === "disputes_prior" ? styles.disputeChip : styles.stuckChip}
                >
                  <span
                    aria-hidden="true"
                    style={ui.badgeDot(
                      f.category === "disputes_prior" ? pwc.error : pwc.warning,
                    )}
                  />
                  {flagKindLabel(f.category)}
                </span>
                <span style={styles.dim}>{humanize(f.status)}</span>
                {f.target_sheet && f.target_row != null && onSelectTarget && (
                  <button
                    type="button"
                    style={styles.linkBtn}
                    onClick={() => onSelectTarget(f.target_sheet as string, f.target_row as number)}
                  >
                    {f.target_sheet} row {f.target_row}
                  </button>
                )}
              </div>
              <p style={styles.flagReason}>{f.reasoning}</p>
              {f.applied_fix && (
                <p style={styles.dim}>Applied fix: {f.applied_fix}</p>
              )}
              {f.human_answer ? (
                <p style={styles.answerGiven}>Your answer: {f.human_answer}</p>
              ) : (
                <div style={styles.answerRow}>
                  <textarea
                    style={styles.answerBox}
                    placeholder="Answer / guidance for this flag…"
                    value={answers[f.id] || ""}
                    onChange={(e) =>
                      setAnswers((a) => ({ ...a, [f.id]: e.target.value }))
                    }
                    aria-label={`Answer flag ${f.id}`}
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

      {/* Guidance + re-review */}
      <h3 style={styles.h3}>Re-review</h3>
      <textarea
        style={styles.guidanceBox}
        placeholder="Optional guidance for the next pass (e.g. 'the PPE note is on page 44')…"
        value={guidance}
        onChange={(e) => setGuidance(e.target.value)}
        aria-label="Re-review guidance"
      />
      <div style={styles.reviewControls}>
        <label style={styles.modelLabel}>
          Model
          <select
            style={styles.modelSelect}
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            aria-label="Reviewer model"
          >
            {/* If the configured default isn't in the list yet, still show it. */}
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
          {busy === "review" ? "Reviewing…" : "Re-review"}
        </button>
      </div>
      {busy === "review" && (
        <p style={styles.dim} role="status">
          The reviewer reads the PDF and traces each failure — this can take a
          few minutes. Leave this tab open; results appear when it finishes.
        </p>
      )}
    </div>
  );
}

const styles = {
  dim: { color: pwc.grey500, fontSize: 13 },
  error: { color: pwc.errorText, fontSize: 13 },
  notice: {
    ...ui.alertInfo,
    padding: pwc.space.sm,
    fontSize: 13,
    margin: `0 0 ${pwc.space.md}px`,
  } as const,
  // Soft warning banner (items 11/12): stale totals / stale download. Warning
  // left-rule, distinct from the info `notice` and the red `error`.
  warning: {
    ...ui.alertWarning,
    padding: pwc.space.sm,
    fontSize: 13,
    margin: `0 0 ${pwc.space.md}px`,
  } as const,
  reviewControls: {
    display: "flex",
    alignItems: "flex-end",
    gap: pwc.space.md,
  } as const,
  modelLabel: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    fontSize: 12,
    color: pwc.grey700,
    fontWeight: 600,
  } as const,
  modelSelect: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    minWidth: 220,
  } as const,
  headerRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: pwc.space.md,
  } as const,
  badge: {
    ...ui.badge,
    borderColor: pwc.info,
  } as const,
  h3: {
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: 600,
    color: pwc.grey900,
    margin: `${pwc.space.lg}px 0 ${pwc.space.sm}px`,
  } as const,
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    fontSize: 13,
  } as const,
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
  newVal: { color: pwc.successText, fontWeight: 600 },
  arrow: { color: pwc.grey500 },
  linkBtn: {
    background: "none",
    border: "none",
    color: pwc.info,
    cursor: "pointer",
    padding: 0,
    textDecoration: "underline",
    fontSize: 13,
  } as const,
  flagStack: { display: "flex", flexDirection: "column" as const, gap: pwc.space.sm },
  flagCard: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    padding: pwc.space.md,
    background: pwc.grey50,
  } as const,
  flagHead: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    marginBottom: pwc.space.xs,
  } as const,
  stuckChip: {
    ...ui.badge,
    borderColor: pwc.warning,
  } as const,
  disputeChip: {
    ...ui.badge,
    borderColor: pwc.error,
  } as const,
  flagReason: { color: pwc.grey800, fontSize: 13, margin: `${pwc.space.xs}px 0` },
  answerGiven: { color: pwc.successText, fontSize: 13, margin: 0 },
  answerRow: { display: "flex", gap: pwc.space.sm, alignItems: "flex-start" },
  answerBox: {
    flex: 1,
    minHeight: 48,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    padding: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as const,
  guidanceBox: {
    width: "100%",
    minHeight: 60,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    padding: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    marginBottom: pwc.space.sm,
  } as const,
  reviewBtn: {
    background: pwc.info,
    color: "#fff",
    border: "none",
    borderRadius: pwc.radius.md,
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontWeight: 600,
    cursor: "pointer",
  } as const,
  revertBtn: {
    background: "#fff",
    color: pwc.errorText,
    border: `1px solid ${pwc.errorBorder}`,
    borderRadius: pwc.radius.md,
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontWeight: 600,
    cursor: "pointer",
  } as const,
  smallBtn: {
    background: pwc.info,
    color: "#fff",
    border: "none",
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontWeight: 600,
    cursor: "pointer",
  } as const,
};
