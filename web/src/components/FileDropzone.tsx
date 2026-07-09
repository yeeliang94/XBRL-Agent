import { useCallback, useRef } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";

// ---------------------------------------------------------------------------
// FileDropzone — the shared drag-and-drop + "Choose file" control (E6).
//
// Extracted from UploadPanel so any file input in the app (the Extract-page
// upload, the mTool template picker) gets the same styled dropzone instead of
// a bare browser `<input type="file">`. The component is presentation +
// interaction only: it hands the chosen File to `onFile` and the caller owns
// validation, upload, and busy state.
// ---------------------------------------------------------------------------

interface Props {
  /** `accept` string for the hidden input, e.g. ".pdf,.docx" or ".xlsx". */
  accept: string;
  /** Instruction line shown above the button. */
  label: string;
  /** Button text. Defaults to "Choose file". */
  buttonLabel?: string;
  disabled?: boolean;
  onFile: (file: File) => void;
  /** Accessible label for the hidden input. */
  inputLabel?: string;
  testId?: string;
  /** Optional slot under the button (e.g. an "Uploading…" line). */
  children?: React.ReactNode;
}

export function FileDropzone({
  accept,
  label,
  buttonLabel = "Choose file",
  disabled = false,
  onFile,
  inputLabel = "Choose file",
  testId = "drop-zone",
  children,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) onFile(file);
      // Reset so re-choosing the SAME file still fires onChange.
      e.target.value = "";
    },
    [onFile],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      if (disabled) return;
      const file = e.dataTransfer.files[0];
      if (file) onFile(file);
    },
    [onFile, disabled],
  );

  return (
    <div
      data-testid={testId}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
      style={styles.dropZone}
    >
      <p style={styles.dropText}>{label}</p>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        className={uiClass.btnPrimary}
        style={disabled ? styles.chooseButtonDisabled : styles.chooseButton}
      >
        {buttonLabel}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={handleChange}
        disabled={disabled}
        style={{ display: "none" }}
        aria-label={inputLabel}
      />
      {children}
    </div>
  );
}

const styles = {
  dropZone: {
    border: `2px dashed ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    padding: pwc.space.xxxl,
    textAlign: "center" as const,
    background: pwc.grey50,
  } as React.CSSProperties,
  dropText: {
    fontFamily: pwc.fontBody,
    color: pwc.grey700,
    fontSize: 15,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  chooseButton: {
    ...ui.buttonPrimary,
    cursor: "pointer",
  } as React.CSSProperties,
  chooseButtonDisabled: {
    ...ui.buttonPrimary,
    cursor: "not-allowed",
    opacity: 0.5,
  } as React.CSSProperties,
};
