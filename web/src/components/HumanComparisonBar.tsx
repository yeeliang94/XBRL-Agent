import { useEffect, useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { userMessage } from "../lib/errors";
import { deleteHumanFile, type HumanFileRecord, type ComparisonHighlight } from "../lib/humanFile";
import { ConfirmDialog } from "./ConfirmDialog";

// ---------------------------------------------------------------------------
// HumanComparisonBar — one compact row above the Figures and Notes work
// surface: the [ Human file | Source PDF ] switch, a one-line summary of the
// comparison, and the attached file with Replace / Remove beside it.
// ---------------------------------------------------------------------------

export type ComparisonPane = "human" | "pdf";

/** Input-only agreement, with excluded values kept separate. */
export interface ComparisonSummary {
  kind: "figures" | "notes";
  scope: string;
  humanFilled: number;
  bothFilled: number;
  matched: number;
  aiOnly: number;
  notCounted: number;
}

interface Props {
  runId: number;
  file: HumanFileRecord;
  showDetails: boolean;
  /** The pane switch, rendered first. */
  paneSwitch: React.ReactNode;
  summary: ComparisonSummary;
  highlights: readonly ComparisonHighlight[];
  onToggleHighlight: (status: ComparisonHighlight) => void;
  onlyHighlighted: boolean;
  onOnlyHighlightedChange: (only: boolean) => void;
  controlsBlocked?: boolean;
  /** Tooltip naming what the "not counted" figure excludes, e.g.
   *  "Not counted: 1 unmatched row"; omitted when nothing is excluded. */
  excludesNote?: string | null;
  onReplace?: () => void;
  onRemoved?: () => void;
}

export function HumanComparisonBar({
  runId, file, showDetails, paneSwitch, summary, highlights, onToggleHighlight,
  onlyHighlighted, onOnlyHighlightedChange, controlsBlocked, excludesNote, onReplace, onRemoved,
}: Props) {
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scoreDetails = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !scoreDetails.current?.contains(event.target) && scoreDetails.current) scoreDetails.current.open = false;
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && scoreDetails.current?.open) {
        scoreDetails.current.open = false;
        scoreDetails.current.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);
  const percentage = (numerator: number, denominator: number) => denominator > 0
    ? `${(100 * numerator / denominator).toFixed(1)}%` : "—";
  const counts: { status: ComparisonHighlight; label: string; value: number }[] = [
    { status: "agree", label: summary.kind === "notes" ? "Placed" : "Match", value: summary.matched },
    ...(summary.kind === "figures" ? [{ status: "different" as const, label: "Different", value: summary.bothFilled - summary.matched }] : []),
    { status: "missed", label: "Missed by AI", value: summary.humanFilled - summary.bothFilled },
    { status: "ai_only", label: "AI-only", value: summary.aiOnly },
    { status: "not_counted", label: "Not counted", value: summary.notCounted },
  ];

  return (
    <section data-testid="human-comparison-bar" aria-label="Human file comparison" style={styles.bar}>
      <div style={styles.row}>
        {paneSwitch}
        {showDetails && (
          <div style={styles.summary}>
            <span><strong style={styles.summaryValue}>{percentage(summary.matched, summary.humanFilled)}</strong>{" "}
              {summary.kind === "figures" ? "figure agreement" : "note placement"}</span>
            {summary.kind === "figures" && <span style={ui.metadata}>{summary.scope}</span>}
            <details className="human-comparison-score" ref={scoreDetails} style={{ position: "relative", maxWidth: "100%" }}>
              <summary className={uiClass.btnQuiet} style={{ ...ui.buttonQuiet, ...ui.buttonSm, listStyle: "none" }}>Score details</summary>
              <div className="human-comparison-score-panel" style={styles.scorePanel}>
                <dl style={styles.scoreValues}>
                  <dt>Exact {summary.kind === "figures" ? "matches" : "placements"}</dt><dd style={{ margin: 0 }}>{summary.matched} / {summary.humanFilled}</dd>
                  <dt>Coverage</dt><dd style={{ margin: 0 }}>{percentage(summary.bothFilled, summary.humanFilled)}</dd>
                  {summary.kind === "figures" && <><dt>Correctness</dt><dd style={{ margin: 0 }}>{percentage(summary.matched, summary.bothFilled)}</dd></>}
                </dl>
                {summary.kind === "figures" && <p style={ui.metadata}>Coverage × correctness. AI-only values and totals are unscored.</p>}
                {excludesNote && <p style={ui.metadata}>{excludesNote}</p>}
                <p style={{ ...ui.metadata, marginBottom: 0 }}>Notes: placement only.</p>
              </div>
            </details>
          </div>
        )}
        {showDetails && (
          <div className="human-comparison-file" style={styles.file} data-testid="human-file-header">
            <span style={styles.fileName}
              title={[file.filename, file.uploaded_by, new Date(file.uploaded_at).toLocaleDateString()].filter(Boolean).join(" · ")}>
              {file.filename}
            </span>
            {onReplace && (
              <button type="button" className={uiClass.btnQuiet}
                disabled={controlsBlocked} style={{ ...ui.buttonQuiet, ...ui.buttonSm }} onClick={onReplace}>
                Replace
              </button>
            )}
            <button type="button" className={uiClass.btnQuiet}
              data-testid="human-file-remove"
              disabled={controlsBlocked}
              style={{ ...ui.buttonQuiet, ...ui.buttonSm }} onClick={() => setConfirmRemove(true)}>
              Remove
            </button>
          </div>
        )}
      </div>
      {showDetails && <div style={styles.controls} data-testid="human-comparison-tiles">
        <div role="group" aria-label="Highlight cells" style={styles.highlightGroup}>
          {counts.map(({ status, label, value }) => <button key={status} type="button"
            className={uiClass.btnQuiet} aria-pressed={highlights.includes(status)} disabled={controlsBlocked}
            title={status === "not_counted" ? excludesNote ?? undefined : undefined}
            style={{ ...ui.segmentOption, ...(highlights.includes(status) ? ui.segmentOptionActive : null) }}
            onClick={() => onToggleHighlight(status)}>{label} {value}</button>)}
        </div>
        <label style={styles.onlyHighlighted}>
          <input type="checkbox" style={ui.checkbox} checked={onlyHighlighted} disabled={controlsBlocked}
            onChange={(event) => onOnlyHighlightedChange(event.target.checked)} />
          Only highlighted
        </label>
      </div>}
      {error && <div role="alert" style={ui.alertError}>{error}</div>}
      <ConfirmDialog
        isOpen={confirmRemove}
        title="Remove the human file?"
        message={<>This removes <strong>{file.filename}</strong> and its comparison from this run.</>}
        confirmLabel="Remove file"
        busyLabel="Removing…"
        busy={removing}
        onCancel={() => setConfirmRemove(false)}
        onConfirm={async () => {
          setRemoving(true);
          setError(null);
          try {
            await deleteHumanFile(runId);
            setConfirmRemove(false);
            onRemoved?.();
          } catch (err) {
            setConfirmRemove(false);
            setError(userMessage(err));
          } finally {
            setRemoving(false);
          }
        }}
      />
    </section>
  );
}

const styles = {
  bar: {
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  row: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    columnGap: pwc.space.lg,
    rowGap: pwc.space.sm,
  } as React.CSSProperties,
  summary: {
    margin: 0,
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    gap: pwc.space.md,
    position: "relative",
    flex: "1 1 280px",
    minWidth: 0,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
    fontVariantNumeric: "tabular-nums",
  } as React.CSSProperties,
  summaryValue: {
    color: pwc.grey900,
    fontWeight: pwc.weight.medium,
    fontSize: 16,
  } as React.CSSProperties,
  file: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
    minWidth: 0,
    marginLeft: "auto",
  } as React.CSSProperties,
  fileName: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    minWidth: 0,
    maxWidth: 180,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    marginRight: pwc.space.xs,
  } as React.CSSProperties,
  controls: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: pwc.space.md, marginTop: pwc.space.md } as React.CSSProperties,
  highlightGroup: { ...ui.segmentControl, flexWrap: "wrap", maxWidth: "100%" } as React.CSSProperties,
  onlyHighlighted: { display: "flex", alignItems: "center", gap: pwc.space.sm, fontSize: 14, minHeight: 40 } as React.CSSProperties,
  scorePanel: { position: "absolute", top: "100%", left: 0, zIndex: 40, width: 300, maxWidth: "calc(100vw - 64px)", padding: pwc.space.md, background: pwc.white, border: `1px solid ${pwc.grey300}`, borderRadius: pwc.radius.md, boxShadow: pwc.shadow.elevated } as React.CSSProperties,
  scoreValues: { display: "grid", gridTemplateColumns: "1fr auto", gap: `${pwc.space.sm}px ${pwc.space.md}px`, margin: 0, fontSize: 14, fontVariantNumeric: "tabular-nums" } as React.CSSProperties,
};
