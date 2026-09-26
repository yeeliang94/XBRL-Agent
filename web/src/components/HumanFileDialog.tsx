import { useEffect, useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { userMessage } from "../lib/errors";
import { DENOMINATION_LABELS } from "../lib/types";
import {
  uploadHumanFile,
  type HumanFileRecord,
  type HumanUnit,
} from "../lib/humanFile";

// ---------------------------------------------------------------------------
// HumanFileDialog — attach (or replace) the mTool file a person filled for the
// same document as this run. The only input besides the file is its unit,
// defaulting to the run's denomination. Standard, level and variants come from
// the run on the server. Uses the shared dialog geometry (ui.scrim/ui.dialog).
// ---------------------------------------------------------------------------

interface Props {
  runId: number;
  open: boolean;
  /** The run's denomination; the unit field defaults to it. */
  defaultUnit: HumanUnit;
  /** The file already attached, if any. Replacing it asks for confirmation. */
  existing: HumanFileRecord | null;
  onClose: () => void;
  /** Called with the stored record after a successful upload. */
  onAttached: (record: HumanFileRecord) => void;
}

const UNITS: HumanUnit[] = ["units", "thousands", "millions"];

export function HumanFileDialog({ runId, open, defaultUnit, existing, onClose, onAttached }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [unit, setUnit] = useState<HumanUnit>(existing?.unit ?? defaultUnit);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<HumanFileRecord | null>(null);
  // Fixed when the dialog opens, so a finished upload keeps its title.
  const [replacing, setReplacing] = useState<HumanFileRecord | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setFile(null);
    setUnit(existing?.unit ?? defaultUnit);
    setConfirmReplace(false);
    setBusy(false);
    setError(null);
    setDone(null);
    setReplacing(existing);
    // Reset only when the dialog opens; `existing` changes after an upload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, busy, onClose]);

  if (!open) return null;

  const submit = async () => {
    if (!file) return;
    if (replacing && !confirmReplace) {
      setConfirmReplace(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { file: record } = await uploadHumanFile(runId, file, unit);
      setDone(record);
      onAttached(record);
    } catch (err) {
      setError(userMessage(err));
      setConfirmReplace(false);
    } finally {
      setBusy(false);
    }
  };

  const warning = done?.summary.magnitude_warning;

  return (
    <div
      style={ui.scrim}
      className="pwc-dialog-scrim-enter"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Compare with human file"
    >
      <div style={{ ...ui.dialog, maxWidth: 480 }} className="pwc-dialog-enter">
        <h2 style={{ ...ui.dialogTitle, marginBottom: pwc.space.md }}>
          {replacing ? "Replace human file" : "Compare with human file"}
        </h2>
        {done ? (
          <div data-testid="human-file-success" style={styles.stack}>
            <p role="status" style={styles.body}>
              Read {done.summary.typed_values ?? 0} figure
              {(done.summary.typed_values ?? 0) === 1 ? "" : "s"} and {done.summary.notes ?? 0} note
              {(done.summary.notes ?? 0) === 1 ? "" : "s"} from <strong>{done.filename}</strong>.
            </p>
            {warning && (
              <div role="alert" style={ui.alertWarning} data-testid="human-file-magnitude-warning">
                {warning}
              </div>
            )}
            <div style={{ ...ui.dialogActionBar, marginTop: 0 }}>
              <button type="button" className={uiClass.btnPrimary}
                style={{ ...ui.buttonPrimary, ...ui.buttonSm }} onClick={onClose}>
                View comparison
              </button>
            </div>
          </div>
        ) : (
          <div style={styles.stack}>
            <div style={styles.field}>
              <label htmlFor="human-file-input" style={ui.fieldLabel}>mTool file</label>
              <input
                ref={fileRef}
                id="human-file-input"
                data-testid="human-file-input"
                type="file"
                accept=".xlsx,.xlsm"
                disabled={busy}
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setConfirmReplace(false);
                  setError(null);
                }}
              />
            </div>
            <div style={styles.field}>
              <label htmlFor="human-file-unit" style={ui.fieldLabel}>Unit of the file&apos;s figures</label>
              <select
                id="human-file-unit"
                data-testid="human-file-unit"
                value={unit}
                disabled={busy}
                onChange={(e) => setUnit(e.target.value as HumanUnit)}
                style={ui.select}
              >
                {UNITS.map((u) => (
                  <option key={u} value={u}>{DENOMINATION_LABELS[u]}</option>
                ))}
              </select>
            </div>
            {confirmReplace && replacing && (
              <div role="alert" style={ui.alertWarning} data-testid="human-file-replace-warning">
                <span>This replaces <strong>{replacing.filename}</strong> and its figures and notes.</span>
              </div>
            )}
            {error && (
              <div role="alert" style={ui.alertError} data-testid="human-file-error">{error}</div>
            )}
            <div style={{ ...ui.dialogActionBar, marginTop: 0, gap: pwc.space.sm }}>
              <button type="button" className={uiClass.btnSecondary}
                style={{ ...ui.buttonSecondary, ...ui.buttonSm }} onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button
                type="button"
                data-testid="human-file-submit"
                className={uiClass.btnPrimary}
                style={{ ...ui.buttonPrimary, ...ui.buttonSm }}
                onClick={() => void submit()}
                disabled={!file || busy}
              >
                {busy ? "Reading file…" : confirmReplace ? "Replace file" : "Compare"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const styles = {
  stack: { display: "flex", flexDirection: "column", gap: pwc.space.lg } as React.CSSProperties,
  field: { display: "flex", flexDirection: "column", gap: pwc.space.xs } as React.CSSProperties,
  body: { ...ui.bodyText, margin: 0 } as React.CSSProperties,
};
