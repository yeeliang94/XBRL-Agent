import { useEffect, useMemo, useState } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { pdfPageUrl, fetchPdfPageCount } from "../lib/api";

// ---------------------------------------------------------------------------
// PdfSourcePane — shows a rendered source-PDF page beside the value grid so a
// reviewer can verify a figure against the document without leaving the page.
//
// Pages are served as plain PNGs (server-rendered via PyMuPDF), so this is
// just an <img> with paging + zoom — no browser PDF library (gotcha #7: the
// frontend avoids heavyweight deps that misbehaved on Windows).
//
// `pages` are the page numbers cited in the selected value's evidence. When
// present, prev/next step through that cited set and the numbers show as
// clickable chips. When empty, the user can still page through the whole
// document via the manual jumper — bad/absent evidence is never fatal.
// ---------------------------------------------------------------------------

export interface PdfSourcePaneProps {
  runId: number;
  // Cited pages for the current selection (from parseEvidencePages). Empty
  // when the selected value has no parseable evidence.
  pages: number[];
  // Total pages, if the parent already knows it. Otherwise the pane fetches
  // it once so the manual jumper and free paging can bound themselves.
  totalPages?: number;
  // True when the pane sits inside a workspace column that already carries
  // its own "Source PDF" header + Hide control (ConceptsPage). Suppresses the
  // pane's internal title and Show/Hide toggle so the same label and the same
  // action don't appear twice in one panel (run-168 design critique).
  embedded?: boolean;
  // Whether anything is currently selected in the parent surface. When false
  // and there are no cited pages, the pane shows a neutral "select a figure"
  // prompt instead of "No source page recorded" — which read as an error
  // before the user had done anything. Defaults true (existing callers only
  // render the pane once a target is selected).
  hasSelection?: boolean;
}

export function PdfSourcePane({
  runId,
  pages,
  totalPages,
  embedded = false,
  hasSelection = true,
}: PdfSourcePaneProps) {
  // Resolved page count: prop wins, else fetched. null = unknown / no PDF.
  const [resolvedTotal, setResolvedTotal] = useState<number | null>(
    totalPages ?? null
  );
  // null when the run has no stored source PDF (legacy / CLI run).
  const [hasPdf, setHasPdf] = useState<boolean>(true);
  const [current, setCurrent] = useState<number | null>(pages[0] ?? null);
  const [imgState, setImgState] = useState<"loading" | "ok" | "error">("loading");
  // Bumping this forces the <img> to remount so a failed load can be retried.
  const [retryKey, setRetryKey] = useState(0);
  const [zoom, setZoom] = useState(1);
  // M3.11 — on narrow viewports the three-region layout has no room for a
  // third column, so the pane defaults collapsed to a toggle. matchMedia is
  // guarded for jsdom (test env), where it's undefined.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia("(max-width: 900px)").matches;
  });

  // Fetch the page count once (only when the parent didn't supply it).
  useEffect(() => {
    if (totalPages != null) {
      setResolvedTotal(totalPages);
      return;
    }
    let cancelled = false;
    fetchPdfPageCount(runId).then((count) => {
      if (cancelled) return;
      setResolvedTotal(count);
      setHasPdf(count != null);
    });
    return () => {
      cancelled = true;
    };
  }, [runId, totalPages]);

  // When the selection changes (new cited pages), jump to the first cited
  // page. If there are no cited pages, default to page 1 so the reviewer
  // still has somewhere to start paging from.
  //
  // Keyed on a STABLE string of the page list — not the array identity.
  // Callers commonly pass `parseEvidencePages(...)`, a fresh array every
  // render, so depending on the array would reset the viewer's current page
  // and zoom on every unrelated parent re-render (search keystroke, value
  // edit, conflict reload).
  const pagesKey = pages.join(",");
  useEffect(() => {
    const next = pages[0] ?? (resolvedTotal ? 1 : null);
    setCurrent(next);
    setZoom(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pagesKey is the
    // stable stand-in for `pages`; depending on `pages` itself defeats the fix.
  }, [pagesKey, resolvedTotal]);

  // Reset the load state whenever the page or a retry changes.
  useEffect(() => {
    setImgState("loading");
  }, [current, retryKey]);

  // Navigation set: the cited pages when present, otherwise the whole doc.
  const navList = useMemo(() => {
    if (pages.length > 0) return pages;
    if (resolvedTotal) {
      return Array.from({ length: resolvedTotal }, (_, i) => i + 1);
    }
    return [];
  }, [pages, resolvedTotal]);

  const idx = current == null ? -1 : navList.indexOf(current);
  // Prev/next step through navList when the current page is in it; if the
  // user manually jumped outside the cited set, fall back to ±1 paging.
  const canPrev =
    current != null && (idx > 0 || (idx === -1 && current > 1));
  const canNext =
    current != null &&
    ((idx >= 0 && idx < navList.length - 1) ||
      (idx === -1 && resolvedTotal != null && current < resolvedTotal));

  function goPrev() {
    if (current == null) return;
    if (idx > 0) setCurrent(navList[idx - 1]);
    else if (idx === -1 && current > 1) setCurrent(current - 1);
  }
  function goNext() {
    if (current == null) return;
    if (idx >= 0 && idx < navList.length - 1) setCurrent(navList[idx + 1]);
    else if (idx === -1 && resolvedTotal != null && current < resolvedTotal)
      setCurrent(current + 1);
  }
  function jumpTo(raw: string) {
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 1) return;
    const clamped = resolvedTotal ? Math.min(n, resolvedTotal) : n;
    setCurrent(clamped);
  }

  // Embedded panes have no internal Show/Hide toggle — the workspace column
  // header owns hiding — so their content must never be stuck collapsed by
  // the narrow-viewport default.
  const isCollapsed = embedded ? false : collapsed;

  // No source PDF for this run — show a quiet empty state, not an error.
  if (!hasPdf) {
    return (
      <section style={styles.panel} data-testid="pdf-source-pane">
        {!embedded && <h2 style={styles.title}>Source PDF</h2>}
        <p style={styles.muted}>
          No source PDF is stored for this run, so side-by-side verification
          isn't available here.
        </p>
      </section>
    );
  }

  return (
    <section style={styles.panel} data-testid="pdf-source-pane">
      <div style={embedded ? styles.headerRowEmbedded : styles.headerRow}>
        {!embedded && <h2 style={styles.title}>Source PDF</h2>}
        <div style={styles.zoomGroup}>
          {!isCollapsed && (
            <>
              <button
                type="button"
                data-testid="pdf-zoom-fit"
                onClick={() => setZoom(1)}
                style={styles.iconButton}
                title="Fit to width"
              >
                Fit
              </button>
              <button
                type="button"
                data-testid="pdf-zoom-in"
                onClick={() => setZoom((z) => Math.min(z + 0.5, 3))}
                style={styles.iconButton}
                title="Zoom in"
              >
                +
              </button>
            </>
          )}
          {!embedded && (
            <button
              type="button"
              data-testid="pdf-collapse-toggle"
              onClick={() => setCollapsed((c) => !c)}
              style={styles.iconButton}
              title={collapsed ? "Show source page" : "Hide source page"}
            >
              {collapsed ? "Show" : "Hide"}
            </button>
          )}
        </div>
      </div>

      {isCollapsed ? null : (
        <>

      {pages.length === 0 &&
        (hasSelection ? (
          <p style={styles.mutedSmall} data-testid="pdf-no-evidence">
            No source page recorded for this value — jump to a page manually.
          </p>
        ) : (
          <p style={styles.mutedSmall} data-testid="pdf-no-selection">
            Select a figure or note to see the page it came from.
          </p>
        ))}

      {pages.length > 0 && (
        <div style={styles.chipRow} data-testid="pdf-cited-chips">
          <span style={styles.chipLabel}>Cited:</span>
          {pages.map((p) => (
            <button
              key={p}
              type="button"
              data-testid={`pdf-cited-${p}`}
              onClick={() => setCurrent(p)}
              style={{
                ...styles.chip,
                background: p === current ? pwc.orange50 : pwc.white,
                borderColor: p === current ? pwc.orange400 : pwc.grey300,
              }}
            >
              {p}
            </button>
          ))}
        </div>
      )}

      <div style={styles.navRow}>
        <button
          type="button"
          data-testid="pdf-prev"
          onClick={goPrev}
          disabled={!canPrev}
          style={{ ...styles.navButton, opacity: canPrev ? 1 : 0.4 }}
        >
          ‹ Prev
        </button>
        <span style={styles.pageIndicator}>
          <input
            data-testid="pdf-page-input"
            inputMode="numeric"
            value={current ?? ""}
            onChange={(e) => jumpTo(e.target.value)}
            style={styles.pageInput}
          />
          {resolvedTotal != null && (
            <span style={styles.pageTotal}> / {resolvedTotal}</span>
          )}
        </span>
        <button
          type="button"
          data-testid="pdf-next"
          onClick={goNext}
          disabled={!canNext}
          style={{ ...styles.navButton, opacity: canNext ? 1 : 0.4 }}
        >
          Next ›
        </button>
      </div>

      <div style={styles.viewport}>
        {current == null ? (
          <p style={styles.muted}>Select a value to view its source page.</p>
        ) : imgState === "error" ? (
          <div style={styles.errorBox} data-testid="pdf-error">
            <p style={styles.mutedSmall}>Couldn't load page {current}.</p>
            <button
              type="button"
              data-testid="pdf-retry"
              onClick={() => setRetryKey((k) => k + 1)}
              style={styles.navButton}
            >
              Retry
            </button>
          </div>
        ) : (
          <img
            key={`${current}-${retryKey}`}
            data-testid="pdf-page-image"
            src={pdfPageUrl(runId, current)}
            alt={`Source PDF page ${current}`}
            onLoad={() => setImgState("ok")}
            onError={() => setImgState("error")}
            style={{
              width: `${zoom * 100}%`,
              maxWidth: zoom === 1 ? "100%" : "none",
              display: "block",
              borderRadius: pwc.radius.sm,
              border: `1px solid ${pwc.grey200}`,
            }}
          />
        )}
      </div>
        </>
      )}
    </section>
  );
}

const styles = {
  panel: {
    ...ui.card,
    padding: pwc.space.lg,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  headerRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  } as React.CSSProperties,
  // Embedded (workspace) variant: no title on the left, so the zoom controls
  // right-align on their own row.
  headerRowEmbedded: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
  } as React.CSSProperties,
  title: {
    margin: 0,
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
  } as React.CSSProperties,
  zoomGroup: { display: "flex", gap: pwc.space.xs } as React.CSSProperties,
  iconButton: {
    ...ui.buttonSecondary,
    minHeight: 28,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    fontSize: 12,
  } as React.CSSProperties,
  muted: {
    margin: 0,
    color: pwc.grey700,
    fontSize: 13,
    lineHeight: 1.5,
  } as React.CSSProperties,
  mutedSmall: {
    margin: 0,
    color: pwc.grey700,
    fontSize: 12,
    lineHeight: 1.45,
  } as React.CSSProperties,
  chipRow: {
    display: "flex",
    flexWrap: "wrap" as const,
    alignItems: "center",
    gap: pwc.space.xs,
  } as React.CSSProperties,
  chipLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: pwc.grey700,
    textTransform: "uppercase" as const,
    letterSpacing: "0.03em",
  } as React.CSSProperties,
  chip: {
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    padding: `2px ${pwc.space.sm}px`,
    fontFamily: pwc.fontMono,
    fontSize: 12,
    cursor: "pointer",
  } as React.CSSProperties,
  navRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  navButton: {
    ...ui.buttonSecondary,
    minHeight: 30,
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontSize: 12,
  } as React.CSSProperties,
  pageIndicator: {
    display: "inline-flex",
    alignItems: "center",
    fontSize: 13,
    color: pwc.grey800,
  } as React.CSSProperties,
  pageInput: {
    width: 56,
    textAlign: "center" as const,
    padding: `${pwc.space.xs}px ${pwc.space.xs}px`,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    fontFamily: pwc.fontMono,
    fontSize: 13,
  } as React.CSSProperties,
  pageTotal: { color: pwc.grey700, marginLeft: 4 } as React.CSSProperties,
  viewport: {
    overflow: "auto",
    maxHeight: "70vh",
    background: pwc.grey50,
    borderRadius: pwc.radius.sm,
    padding: pwc.space.sm,
    minHeight: 120,
    display: "flex",
    justifyContent: "center",
    alignItems: "flex-start",
  } as React.CSSProperties,
  errorBox: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    gap: pwc.space.sm,
    padding: pwc.space.lg,
  } as React.CSSProperties,
} as const;
