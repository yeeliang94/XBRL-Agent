import { useCallback, useEffect, useRef, useState } from "react";
import { pwc } from "../lib/theme";
import {
  startDocConvert,
  getDocConvertStatus,
  docConvertViewUrl,
  docConvertDocxUrl,
  type DocConvertStatus,
} from "../lib/api";

// ---------------------------------------------------------------------------
// ReadableDocPage — the standalone "scanned PDF → readable document" surface
// (docs/PLAN-scanned-pdf-to-doc.md). Upload a scanned PDF, convert it in the
// background, then read/copy the result in-app or download it as Word.
//
// Independent of the extraction pipeline. Inline styles + pwc tokens
// (CLAUDE.md gotcha #7 — no Tailwind). Progress is polled (the backend also
// exposes an SSE stream; polling is simpler for a single standalone job).
// ---------------------------------------------------------------------------

type Phase = "idle" | "converting" | "done" | "failed";

const POLL_INTERVAL_MS = 800;

export function ReadableDocPage() {
  const [file, setFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [status, setStatus] = useState<DocConvertStatus | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Stop polling if the user navigates away mid-conversion.
  useEffect(() => {
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  const poll = useCallback((id: number) => {
    getDocConvertStatus(id)
      .then((s) => {
        setStatus(s);
        if (s.status === "done") {
          setPhase("done");
        } else if (s.status === "failed") {
          setPhase("failed");
          setError(s.error || "Conversion failed.");
        } else {
          // Still queued/running — schedule the next poll.
          pollTimer.current = setTimeout(() => poll(id), POLL_INTERVAL_MS);
        }
      })
      .catch((e) => {
        setPhase("failed");
        setError(e instanceof Error ? e.message : "Could not check progress.");
      });
  }, []);

  const onConvert = useCallback(() => {
    if (!file) return;
    setPhase("converting");
    setError(null);
    setStatus(null);
    startDocConvert(file)
      .then((r) => {
        setJobId(r.job_id);
        poll(r.job_id); // first poll immediately
      })
      .catch((e) => {
        setPhase("failed");
        setError(e instanceof Error ? e.message : "Could not start the conversion.");
      });
  }, [file, poll]);

  const reset = useCallback(() => {
    if (pollTimer.current) clearTimeout(pollTimer.current);
    setFile(null);
    setJobId(null);
    setStatus(null);
    setPhase("idle");
    setError(null);
  }, []);

  const converting = phase === "converting";
  const progressLabel =
    status && status.total_pages > 0
      ? `Converting page ${status.current_page} of ${status.total_pages}…`
      : "Starting conversion…";

  return (
    <div style={styles.wrap}>
      <h1 style={styles.title}>Readable Document</h1>
      <p style={styles.subtitle}>
        Turn a scanned PDF into a readable, copy-pasteable document. Runs locally —
        nothing leaves this server.
      </p>

      {/* Upload + Convert */}
      <div style={styles.card}>
        <input
          type="file"
          accept="application/pdf,.pdf"
          aria-label="Choose a PDF to convert"
          disabled={converting}
          onChange={(e) => {
            const next = e.target.files?.[0] ?? null;
            // Picking a new file after a finished/failed run clears the old
            // result first, then selects the new file.
            if (phase !== "idle") reset();
            setFile(next);
          }}
          style={{ marginBottom: pwc.space.md }}
        />
        <div>
          <button
            type="button"
            onClick={onConvert}
            disabled={!file || converting}
            style={!file || converting ? styles.buttonDisabled : styles.buttonPrimary}
          >
            {converting ? "Converting…" : "Convert"}
          </button>
        </div>
      </div>

      {/* Progress */}
      {converting && (
        <div style={styles.card} role="status" aria-live="polite">
          <div style={styles.progressLabel}>{progressLabel}</div>
          <div style={styles.progressTrack}>
            <div
              style={{
                ...styles.progressBar,
                width:
                  status && status.total_pages > 0
                    ? `${Math.round((status.current_page / status.total_pages) * 100)}%`
                    : "10%",
              }}
            />
          </div>
        </div>
      )}

      {/* Failure */}
      {phase === "failed" && (
        <div style={styles.errorCard} role="alert">
          <strong>Conversion failed.</strong>
          <div style={{ marginTop: pwc.space.xs }}>{error}</div>
          <button type="button" onClick={reset} style={styles.buttonSecondary}>
            Try another file
          </button>
        </div>
      )}

      {/* Result */}
      {phase === "done" && jobId != null && (
        <div>
          <div style={styles.resultToolbar}>
            <span style={styles.copyHint}>
              Select any text or table cells and copy (Ctrl/⌘ + C).
            </span>
            <span style={{ flex: 1 }} />
            <a href={docConvertDocxUrl(jobId)} style={styles.buttonPrimary} download>
              Download as Word
            </a>
            <button type="button" onClick={reset} style={styles.buttonSecondary}>
              Convert another
            </button>
          </div>
          {/* sandbox="" loads the converted HTML in an opaque origin with NO
              scripts and NO same-origin access, so even if the OCR'd HTML ever
              contained active content it cannot run or call authenticated APIs.
              Static HTML + inline CSS still render, and text stays selectable
              for copy-paste. Paired with a restrictive CSP on the /view
              response (docconvert/routes.py). */}
          <iframe
            title="Converted document"
            src={docConvertViewUrl(jobId)}
            sandbox=""
            style={styles.viewer}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles (inline, pwc tokens)
// ---------------------------------------------------------------------------

const buttonBase: React.CSSProperties = {
  padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
  fontFamily: pwc.fontHeading,
  fontSize: 15,
  fontWeight: pwc.weight.medium,
  borderRadius: pwc.radius.md,
  border: "none",
  cursor: "pointer",
  textDecoration: "none",
  display: "inline-block",
};

const styles = {
  wrap: { maxWidth: 980, margin: "0 auto", padding: pwc.space.xl } as React.CSSProperties,
  title: {
    fontFamily: pwc.fontHeading,
    fontSize: 28,
    fontWeight: pwc.weight.light,
    color: pwc.grey900,
    margin: 0,
  } as React.CSSProperties,
  subtitle: {
    fontFamily: pwc.fontBody,
    fontSize: 15,
    color: pwc.grey500,
    marginTop: pwc.space.xs,
    marginBottom: pwc.space.xl,
  } as React.CSSProperties,
  card: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    padding: pwc.space.xl,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  errorCard: {
    background: "#FDF2F0",
    border: `1px solid ${pwc.orange500}`,
    borderRadius: pwc.radius.lg,
    padding: pwc.space.xl,
    marginBottom: pwc.space.lg,
    color: pwc.grey800,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
  buttonPrimary: { ...buttonBase, background: pwc.orange500, color: pwc.white } as React.CSSProperties,
  buttonSecondary: {
    ...buttonBase,
    background: pwc.white,
    color: pwc.grey700,
    border: `1px solid ${pwc.grey300}`,
    marginLeft: pwc.space.sm,
  } as React.CSSProperties,
  buttonDisabled: {
    ...buttonBase,
    background: pwc.grey200,
    color: pwc.grey500,
    cursor: "not-allowed",
  } as React.CSSProperties,
  progressLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
    marginBottom: pwc.space.sm,
  } as React.CSSProperties,
  progressTrack: {
    height: 8,
    background: pwc.grey200,
    borderRadius: pwc.radius.pill,
    overflow: "hidden",
  } as React.CSSProperties,
  progressBar: {
    height: "100%",
    background: pwc.orange500,
    transition: "width 0.3s ease",
  } as React.CSSProperties,
  resultToolbar: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  copyHint: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey500,
  } as React.CSSProperties,
  viewer: {
    width: "100%",
    height: "70vh",
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    background: pwc.white,
  } as React.CSSProperties,
} as const;
