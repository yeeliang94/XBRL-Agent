import { useEffect, useRef, useState } from "react";
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
// `pages` are the physical PDF page numbers cited in the selected value's
// evidence. They are shortcuts, not navigation bounds: previous/next always
// move one page through the document so a reviewer can inspect nearby context.
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
  const sourcesMenuRef = useRef<HTMLDetailsElement | null>(null);
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
  // physical PDF page. Page-count resolution is deliberately NOT a dependency:
  // it often arrives after the image, and used to reset a quick manual move.
  //
  // Keyed on a STABLE string of the page list — not the array identity.
  // Callers commonly pass `parseEvidencePages(...)`, a fresh array every
  // render, so depending on the array would reset the viewer's current page
  // and zoom on every unrelated parent re-render (search keystroke, value
  // edit, conflict reload).
  const pagesKey = pages.join(",");
  useEffect(() => {
    setCurrent(pages[0] ?? null);
    setZoom(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pagesKey is the
    // stable stand-in for `pages`; depending on `pages` itself defeats the fix.
  }, [runId, pagesKey]);

  // Once the page count is known, initialise an evidence-free viewer at page
  // 1. Navigation performed while the count request was in flight is
  // preserved. Deliberately NO clamping here: a cited page beyond the
  // document (bad evidence) must surface as a visible failed page load, not
  // silently open the last page — the reviewer would verify against the
  // wrong page believing it's the citation. Same for a manual overshoot
  // typed before the count arrived: the error state is visible and the page
  // box stays editable. Bad evidence becomes visible, not fatal.
  useEffect(() => {
    if (resolvedTotal == null) return;
    setCurrent((page) => (page == null ? 1 : page));
  }, [runId, pagesKey, resolvedTotal]);

  // Reset the load state whenever the page or a retry changes.
  useEffect(() => {
    setImgState("loading");
  }, [current, retryKey]);

  const canPrev = current != null && current > 1;
  const canNext =
    current != null &&
    resolvedTotal != null &&
    current < resolvedTotal;

  function goPrev() {
    setCurrent((page) => page != null && page > 1 ? page - 1 : page);
  }
  function goNext() {
    setCurrent((page) =>
      page != null && resolvedTotal != null && page < resolvedTotal
        ? page + 1
        : page,
    );
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

  useEffect(() => {
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const menu = sourcesMenuRef.current;
      if (menu?.open && !menu.contains(event.target as Node)) menu.open = false;
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, []);

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
      {!embedded && (
        <div style={styles.headerRow}>
          <h2 style={styles.title}>Source PDF</h2>
          <button
            type="button"
            data-testid="pdf-collapse-toggle"
            onClick={() => setCollapsed((c) => !c)}
            style={styles.iconButton}
            title={collapsed ? "Show source page" : "Hide source page"}
          >
            {collapsed ? "Show" : "Hide"}
          </button>
        </div>
      )}

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

      <div role="toolbar" style={styles.viewerToolbar} aria-label="PDF controls">
        <div style={styles.navGroup}>
          <button
            type="button"
            data-testid="pdf-prev"
            onClick={goPrev}
            disabled={!canPrev}
            aria-label="Previous PDF page"
            data-tooltip="Previous PDF page"
            style={{ ...styles.compactButton, opacity: canPrev ? 1 : 0.4 }}
          >
            ‹
          </button>
          <span style={styles.pageIndicator}>
            <input
              data-testid="pdf-page-input"
              inputMode="numeric"
              aria-label="PDF page number"
              value={current ?? ""}
              onChange={(e) => jumpTo(e.target.value)}
              style={styles.pageInput}
            />
            {resolvedTotal != null && <span style={styles.pageTotal}>/ {resolvedTotal}</span>}
          </span>
          <button
            type="button"
            data-testid="pdf-next"
            onClick={goNext}
            disabled={!canNext}
            aria-label="Next PDF page"
            data-tooltip="Next PDF page"
            style={{ ...styles.compactButton, opacity: canNext ? 1 : 0.4 }}
          >
            ›
          </button>
        </div>

        {pages.length > 0 && (
          <details ref={sourcesMenuRef} style={styles.sourcesMenu} data-testid="pdf-cited-chips">
            <summary style={styles.sourcesSummary}>Sources ({pages.length})</summary>
            <div style={styles.sourcesPanel}>
              {pages.map((page) => (
                <button
                  key={page}
                  type="button"
                  data-testid={`pdf-cited-${page}`}
                  onClick={() => {
                    setCurrent(page);
                    if (sourcesMenuRef.current) sourcesMenuRef.current.open = false;
                  }}
                  aria-label={`Open cited PDF page ${page}`}
                  style={{
                    ...styles.sourcePageButton,
                    background: page === current ? pwc.grey100 : pwc.white,
                  }}
                >
                  Page {page}
                </button>
              ))}
            </div>
          </details>
        )}

        <div style={styles.zoomGroup}>
          <button
            type="button"
            data-testid="pdf-zoom-out"
            onClick={() => setZoom((value) => Math.max(value - 0.5, 0.5))}
            style={styles.compactButton}
            title="Zoom out"
            aria-label="Zoom out"
            data-tooltip="Zoom out"
          >
            −
          </button>
          <button
            type="button"
            data-testid="pdf-zoom-fit"
            onClick={() => setZoom(1)}
            style={styles.fitButton}
            title="Fit to width"
          >
            Fit
          </button>
          <button
            type="button"
            data-testid="pdf-zoom-in"
            onClick={() => setZoom((value) => Math.min(value + 0.5, 3))}
            style={styles.compactButton}
            title="Zoom in"
            aria-label="Zoom in"
            data-tooltip="Zoom in"
          >
            +
          </button>
        </div>
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
    minHeight: 36,
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
  viewerToolbar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.xs,
    minHeight: 38,
    flexWrap: "wrap" as const,
    paddingBottom: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  navGroup: { display: "flex", alignItems: "center", gap: 2 } as React.CSSProperties,
  navButton: {
    ...ui.buttonSecondary,
    minHeight: 36,
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
    width: 42,
    textAlign: "center" as const,
    padding: `${pwc.space.xs}px 2px`,
    border: "none",
    borderRadius: pwc.radius.sm,
    fontFamily: pwc.fontMono,
    fontSize: 13,
  } as React.CSSProperties,
  pageTotal: { color: pwc.grey700, marginLeft: 4 } as React.CSSProperties,
  compactButton: {
    width: 32,
    minHeight: 32,
    padding: 0,
    border: "none",
    borderRadius: pwc.radius.sm,
    background: "transparent",
    color: pwc.grey900,
    cursor: "pointer",
    fontSize: 15,
  } as React.CSSProperties,
  fitButton: {
    minWidth: 36,
    minHeight: 32,
    padding: `0 ${pwc.space.xs}px`,
    border: "none",
    borderRadius: pwc.radius.sm,
    background: "transparent",
    color: pwc.grey700,
    cursor: "pointer",
    fontSize: 11,
  } as React.CSSProperties,
  sourcesMenu: { position: "relative" as const } as React.CSSProperties,
  sourcesSummary: {
    minHeight: 32,
    padding: `0 ${pwc.space.sm}px`,
    display: "inline-flex",
    alignItems: "center",
    borderRadius: pwc.radius.sm,
    color: pwc.grey700,
    cursor: "pointer",
    listStyle: "none",
    fontSize: 11,
    fontWeight: 600,
  } as React.CSSProperties,
  sourcesPanel: {
    position: "absolute" as const,
    top: 36,
    right: 0,
    zIndex: 20,
    minWidth: 110,
    padding: pwc.space.xs,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    background: pwc.white,
    boxShadow: pwc.shadow.elevated,
  } as React.CSSProperties,
  sourcePageButton: {
    width: "100%",
    minHeight: 32,
    padding: `0 ${pwc.space.sm}px`,
    border: "none",
    borderRadius: pwc.radius.sm,
    color: pwc.grey900,
    textAlign: "left" as const,
    cursor: "pointer",
    fontSize: 12,
  } as React.CSSProperties,
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
