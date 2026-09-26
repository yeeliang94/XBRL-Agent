import { useState } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { userMessage } from "../lib/errors";
import { deleteHumanFile, type HumanFileRecord } from "../lib/humanFile";
import { ConfirmDialog } from "./ConfirmDialog";

// ---------------------------------------------------------------------------
// HumanComparisonBar — one compact row above the Figures and Notes work
// surface: the [ Human file | Source PDF ] switch (supplied by the page so it
// matches the page's other segmented controls), flat stat tiles, and the
// attached file with Replace / Remove beside it.
// ---------------------------------------------------------------------------

export type ComparisonPane = "human" | "pdf";

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
  /** e.g. "Excludes 3 unmatched rows"; omitted when nothing is excluded. */
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
            <div style={styles.file} data-testid="human-file-header">
              <span style={styles.fileName} title={file.filename}>{file.filename}</span>
              <span style={ui.metadata}>
                {[file.uploaded_by, new Date(file.uploaded_at).toLocaleDateString()].filter(Boolean).join(" · ")}
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
      {showDetails && (
        <dl style={styles.tiles} data-testid="human-comparison-tiles">
          {tiles.map((tile) => (
            <div key={tile.label} style={styles.tile}>
              <dt style={ui.microLabel}>{tile.label}</dt>
              <dd style={styles.tileValue}>{tile.value}</dd>
            </div>
          ))}
          {excludesNote && <div style={{ ...ui.metadata, alignSelf: "flex-end" }}>{excludesNote}</div>}
        </dl>
      )}
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
    display: "flex",
    flexDirection: "column",
    gap: pwc.space.md,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  row: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    gap: pwc.space.xl,
  } as React.CSSProperties,
  tiles: {
    display: "flex",
    flexWrap: "wrap",
    gap: pwc.space.xl,
    margin: 0,
  } as React.CSSProperties,
  tile: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
    minWidth: 0,
  } as React.CSSProperties,
  tileValue: {
    margin: 0,
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
    fontVariantNumeric: "tabular-nums",
  } as React.CSSProperties,
  file: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    gap: pwc.space.sm,
    minWidth: 0,
  } as React.CSSProperties,
  fileName: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
    overflowWrap: "anywhere",
  } as React.CSSProperties,
};
