// Shared elapsed-time formatting helpers. Consolidates the near-duplicate
// `formatElapsed` fns that lived in ElapsedTimer and ResultsView.

/**
 * Format a total number of seconds as MM:SS (zero-padded). `null` renders
 * the placeholder `—`. 0 becomes `00:00`; 65 becomes `01:05`.
 */
export function formatMMSS(totalSeconds: number | null): string {
  if (totalSeconds === null) return "—";
  const safe = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** Format a duration in milliseconds as MM:SS. Negative values clamp to 0. */
export function formatElapsedMs(ms: number): string {
  return formatMMSS(Math.floor(ms / 1000));
}
