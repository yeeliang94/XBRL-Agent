import { useEffect, useState, useCallback } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";

// ---------------------------------------------------------------------------
// ReconciliationQueue — side panel listing open conflicts for a run.
// Each item carries a residual and a one-click resolve / dismiss action.
//
// Phase 1 surfaces:
//   - partial_state    — parent + children sums don't reconcile
//   - parent_child_disagree — aggregate_only parent has observed children
//
// Phase 3 will add cross_check_failure once correction migrates.
// ---------------------------------------------------------------------------

export interface ConflictRow {
  id: number;
  concept_uuid: string;
  period: string;
  entity_scope: string;
  kind: string;
  residual: number | null;
  detail: string | null;
  status: string;
  canonical_label?: string | null;
  render_sheet?: string | null;
  render_row?: number | null;
}

export function ReconciliationQueue({
  runId,
  reloadKey,
}: {
  runId: number;
  // Bumped by the parent after a value edit so the queue re-fetches and
  // surfaces (or clears) conflicts the cascade just wrote — without a
  // full-page reload. Phase 2.2.
  reloadKey?: number;
}) {
  const [conflicts, setConflicts] = useState<ConflictRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Action failures (resolve/dismiss) are non-fatal — they show inline
  // without hiding the whole queue, unlike loadError which blocks the
  // panel.  Keeping them separate avoids a failed action nuking the
  // list the user is trying to act on.
  const [actionError, setActionError] = useState<string | null>(null);

  // Peer-review #11: load takes an optional AbortSignal so the mount
  // effect can cancel an in-flight fetch on unmount / runId change.
  const load = useCallback(
    (signal?: AbortSignal) => {
      fetch(`/api/runs/${runId}/conflicts`, { signal })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((data) => {
          setConflicts(
            (data.conflicts || []).filter(
              (c: ConflictRow) => c.status === "open"
            )
          );
        })
        .catch((err) => {
          if (err?.name === "AbortError") return;
          setLoadError(String(err));
        });
    },
    [runId]
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load, reloadKey]);

  const onResolve = useCallback(
    async (id: number, action: "resolved" | "dismissed") => {
      // Peer-review #10: only drop the row after the server confirms.
      // A 500 must leave the conflict in the queue.
      try {
        const resp = await fetch(`/api/conflicts/${id}/resolve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action }),
        });
        if (!resp.ok) {
          setActionError(`Resolve failed (HTTP ${resp.status})`);
          return;
        }
        setActionError(null);
        setConflicts((prev) => prev.filter((c) => c.id !== id));
      } catch (err) {
        setActionError(`Resolve failed: ${String(err)}`);
      }
    },
    []
  );

  if (loadError) {
    return (
      <div
        data-testid="reconciliation-queue-error"
        style={{ color: pwc.error, padding: pwc.space.md }}
      >
        Failed to load conflicts: {loadError}
      </div>
    );
  }

  return (
    <div
      data-testid="reconciliation-queue"
      style={{
        ...ui.card,
        padding: pwc.space.lg,
      }}
    >
      <h2
        style={{
          margin: 0,
          marginBottom: pwc.space.md,
          fontFamily: pwc.fontHeading,
          color: pwc.grey900,
          fontSize: 15,
          fontWeight: 700,
        }}
      >
        Reconciliation queue ({conflicts.length})
      </h2>
      {actionError && (
        <div
          data-testid="reconciliation-action-error"
          style={{
            color: pwc.error,
            fontSize: 12,
            marginBottom: pwc.space.sm,
          }}
        >
          {actionError}
        </div>
      )}
      {conflicts.length === 0 ? (
        <div
          data-testid="reconciliation-empty"
          style={{
            color: pwc.grey700,
            fontSize: 13,
            lineHeight: 1.5,
            padding: `${pwc.space.sm}px 0`,
          }}
        >
          No open conflicts.
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {conflicts.map((c) => (
            <li
              key={c.id}
              data-testid={`conflict-${c.id}`}
              style={{
                padding: `${pwc.space.md}px 0`,
                borderTop: `1px solid ${pwc.grey100}`,
                marginBottom: pwc.space.sm,
              }}
            >
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  lineHeight: 1.4,
                  color: pwc.grey900,
                }}
              >
                {c.canonical_label || c.concept_uuid}
              </div>
              <div style={{ fontSize: 12, color: pwc.grey700 }}>
                {c.kind}
                {c.residual !== null && (
                  <> · residual {c.residual.toFixed(2)}</>
                )}
              </div>
              {c.detail && (
                <div style={{ fontSize: 12, marginTop: 2 }}>{c.detail}</div>
              )}
              <div style={{ marginTop: pwc.space.sm, display: "flex", gap: pwc.space.sm }}>
                <button
                  data-testid={`resolve-btn-${c.id}`}
                  onClick={() => onResolve(c.id, "resolved")}
                  style={{
                    ...ui.buttonPrimary,
                    minHeight: 30,
                    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
                    fontSize: 12,
                  }}
                >
                  Resolve
                </button>
                <button
                  data-testid={`dismiss-btn-${c.id}`}
                  onClick={() => onResolve(c.id, "dismissed")}
                  style={{
                    ...ui.buttonSecondary,
                    minHeight: 30,
                    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
                    fontSize: 12,
                  }}
                >
                  Dismiss
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
