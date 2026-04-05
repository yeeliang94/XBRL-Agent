import { useCallback, useRef, useState } from "react";
import type { UploadResponse } from "../lib/types";
import { pwc } from "../lib/theme";
import { ElapsedTimer } from "./ElapsedTimer";

interface Props {
  onUpload: (file: File) => Promise<UploadResponse>;
  isRunning: boolean;
  filename: string | null;
  onRun: () => void;
  canRun: boolean;
  startTime: number | null;
}

const styles = {
  container: {
    background: pwc.white,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    boxShadow: pwc.shadow.card,
    padding: pwc.space.xl,
  } as React.CSSProperties,
  dropZone: {
    border: `2px dashed ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    padding: pwc.space.xxl,
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
    padding: "10px 24px",
    backgroundColor: pwc.orange500,
    color: pwc.white,
    border: "none",
    borderRadius: pwc.radius.md,
    fontSize: 14,
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    cursor: "pointer",
  } as React.CSSProperties,
  chooseButtonDisabled: {
    padding: "10px 24px",
    backgroundColor: pwc.orange500,
    color: pwc.white,
    border: "none",
    borderRadius: pwc.radius.md,
    fontSize: 14,
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    cursor: "not-allowed",
    opacity: 0.5,
  } as React.CSSProperties,
  fileRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  } as React.CSSProperties,
  fileInfo: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
  } as React.CSSProperties,
  fileIcon: {
    width: 32,
    height: 32,
    background: "#FEE2E2",
    borderRadius: pwc.radius.sm,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: pwc.error,
    fontSize: 11,
    fontWeight: "bold" as const,
    fontFamily: pwc.fontMono,
  } as React.CSSProperties,
  fileName: {
    fontFamily: pwc.fontBody,
    fontWeight: 500,
    color: pwc.grey900,
    fontSize: 15,
  } as React.CSSProperties,
  runButton: {
    padding: "10px 24px",
    backgroundColor: pwc.orange500,
    color: pwc.white,
    border: "none",
    borderRadius: pwc.radius.md,
    fontSize: 14,
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    cursor: "pointer",
  } as React.CSSProperties,
  runningRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  spinner: {
    width: 20,
    height: 20,
    border: `3px solid ${pwc.grey200}`,
    borderTop: `3px solid ${pwc.orange500}`,
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
  } as React.CSSProperties,
  error: {
    fontFamily: pwc.fontBody,
    color: pwc.error,
    fontSize: 14,
    marginTop: pwc.space.sm,
  } as React.CSSProperties,
  uploading: {
    fontFamily: pwc.fontBody,
    color: pwc.grey500,
    fontSize: 14,
    marginTop: pwc.space.sm,
  } as React.CSSProperties,
};

export function UploadPanel({ onUpload, isRunning, filename, onRun, canRun, startTime }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setError("Only PDF files are accepted.");
        return;
      }
      setError(null);
      setUploading(true);
      try {
        await onUpload(file);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [onUpload],
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const disabled = isRunning || uploading;

  return (
    <div style={styles.container}>
      {!filename ? (
        <div
          data-testid="drop-zone"
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
          style={styles.dropZone}
        >
          <p style={styles.dropText}>Drop a PDF here or click the button below</p>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={disabled}
            style={disabled ? styles.chooseButtonDisabled : styles.chooseButton}
          >
            Choose PDF
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf"
            onChange={handleChange}
            disabled={disabled}
            style={{ display: "none" }}
            aria-label="Upload PDF"
          />
          {uploading && <p style={styles.uploading}>Uploading...</p>}
        </div>
      ) : (
        <div style={styles.fileRow}>
          <div style={styles.fileInfo}>
            <div style={styles.fileIcon}>PDF</div>
            <span style={styles.fileName}>{filename}</span>
          </div>

          {canRun && (
            <button onClick={onRun} style={styles.runButton}>
              Run Extraction
            </button>
          )}

          {isRunning && (
            <div style={styles.runningRow}>
              <div data-testid="run-spinner" style={styles.spinner} />
              {startTime && <ElapsedTimer startTime={startTime} isRunning={isRunning} />}
            </div>
          )}
        </div>
      )}
      {error && <p style={styles.error}>{error}</p>}
    </div>
  );
}
