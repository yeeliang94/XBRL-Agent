import { useState } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { userMessage } from "../lib/errors";
import { deleteHumanFile, type HumanFileRecord } from "../lib/humanFile";
import { ConfirmDialog } from "./ConfirmDialog";

// ---------------------------------------------------------------------------
// HumanComparisonBar — one compact row above the Figures and Notes work
// surface: the [ Human file | Source PDF ] switch, a one-line summary of the
// comparison, and the attached file with Replace / Remove beside it.
// ---------------------------------------------------------------------------

export type ComparisonPane = "human" | "pdf";

/** One summary fact, read as "<value> <label>", e.g. "14 differ". */
export interface ComparisonTile {
  label: string;
  value: string;
}

interface Props {
  runId: number;
  file: HumanFileRecord;
  showDetails: boolean;
  /** The pane switch, rendered first. */
  paneSwitch: React.ReactNode;
  tiles: ComparisonTile[];
  /** e.g. "Not counted: 1 unmatched row"; omitted when nothing is excluded. */
  excludesNote?: string | null;
  onReplace?: () => void;
  onRemoved?: () => void;
}

export function HumanComparisonBar({
  runId, file, showDetails, paneSwitch, tiles, excludesNote, onReplace, onRemoved,
}: Props) {
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <section data-testid="human-comparison-bar" aria-label="Human file comparison" style={styles.bar}>
      <div style={styles.row}>
        {paneSwitch}
        {showDetails && (
          <p style={styles.summary} data-testid="human-comparison-tiles" title={excludesNote ?? undefined}>
            {tiles.map((tile, index) => (
              <span key={tile.label}>
                {index > 0 && <span aria-hidden="true" style={styles.separator}>·</span>}
                <strong style={styles.summaryValue}>{tile.value}</strong> {tile.label}
              </span>
            ))}
            {excludesNote && <span style={ui.metadata}>
              <span aria-hidden="true" style={styles.separator}>·</span>
              {excludesNote}
            </span>}
          </p>
        )}
        {showDetails && (
          <div className="human-comparison-file" style={styles.file} data-testid="human-file-header">
            <span style={styles.fileName}
              title={[file.filename, file.uploaded_by, new Date(file.uploaded_at).toLocaleDateString()].filter(Boolean).join(" · ")}>
              {file.filename}
            </span>
            {onReplace && (
              <button type="button" className={uiClass.btnQuiet}
                style={{ ...ui.buttonQuiet, ...ui.buttonSm }} onClick={onReplace}>
                Replace
              </button>
            )}
            <button type="button" className={uiClass.btnQuiet}
              data-testid="human-file-remove"
              style={{ ...ui.buttonQuiet, ...ui.buttonSm }} onClick={() => setConfirmRemove(true)}>
              Remove
            </button>
          </div>
        )}
      </div>
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
    flex: "0 1 auto",
    minWidth: 0,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
    fontVariantNumeric: "tabular-nums",
  } as React.CSSProperties,
  summaryValue: {
    color: pwc.grey900,
    fontWeight: pwc.weight.semibold,
  } as React.CSSProperties,
  separator: {
    margin: `0 ${pwc.space.sm}px`,
    color: pwc.grey300,
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
    maxWidth: 220,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    marginRight: pwc.space.xs,
  } as React.CSSProperties,
};
