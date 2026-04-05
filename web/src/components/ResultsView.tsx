import { useState, useEffect, useCallback } from "react";
import type { CompleteData } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  complete: CompleteData;
  sessionId: string;
  runStartTime: number | null;
  getResultJson: (sessionId: string) => Promise<Record<string, unknown>>;
}

type Tab = "summary" | "preview" | "downloads";

function formatElapsed(startTime: number | null): string {
  if (!startTime) return "--:--";
  const totalSeconds = Math.floor((Date.now() - startTime) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

const styles = {
  container: {
    background: pwc.white,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    boxShadow: pwc.shadow.card,
    overflow: "hidden",
  } as React.CSSProperties,
  tabBar: {
    display: "flex",
    borderBottom: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  tabActive: {
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
    background: "none",
    border: "none",
    borderBottom: `2px solid ${pwc.orange500}`,
    cursor: "pointer",
  } as React.CSSProperties,
  tabInactive: {
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 500,
    color: pwc.grey500,
    background: "none",
    border: "none",
    borderBottom: "2px solid transparent",
    cursor: "pointer",
  } as React.CSSProperties,
  content: {
    padding: pwc.space.xl,
  } as React.CSSProperties,
  // Summary tab
  cardGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
    gap: pwc.space.lg,
  } as React.CSSProperties,
  card: {
    background: pwc.grey50,
    borderRadius: pwc.radius.md,
    padding: pwc.space.lg,
    textAlign: "center" as const,
  } as React.CSSProperties,
  cardLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  cardValue: {
    fontFamily: pwc.fontMono,
    fontSize: 20,
    fontWeight: 600,
    color: pwc.grey900,
  } as React.CSSProperties,
  successBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.xs,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.success,
    background: "#F0FDF4",
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    borderRadius: pwc.radius.md,
  } as React.CSSProperties,
  failBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.xs,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.error,
    background: "#FEF2F2",
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    borderRadius: pwc.radius.md,
  } as React.CSSProperties,
  // Data Preview
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
  th: {
    background: pwc.grey100,
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    color: pwc.grey900,
    fontSize: 13,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    textAlign: "left" as const,
    borderBottom: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  td: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey800,
  } as React.CSSProperties,
  tdEmpty: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey500,
    background: pwc.orange50,
  } as React.CSSProperties,
  loading: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey500,
    textAlign: "center" as const,
    padding: pwc.space.xl,
  } as React.CSSProperties,
  fetchError: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.error,
    textAlign: "center" as const,
    padding: pwc.space.xl,
  } as React.CSSProperties,
  retryButton: {
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    color: pwc.orange500,
    background: "none",
    border: "none",
    cursor: "pointer",
    textDecoration: "underline",
    marginTop: pwc.space.sm,
  } as React.CSSProperties,
  // Downloads
  downloadRow: {
    display: "flex",
    gap: pwc.space.md,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  downloadButton: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 500,
    color: pwc.grey900,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    textDecoration: "none",
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
};

export function ResultsView({ complete, sessionId, runStartTime, getResultJson }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>("summary");
  const [resultData, setResultData] = useState<Record<string, unknown> | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [fetched, setFetched] = useState(false);

  // Fetch result.json on first switch to preview tab.
  // Not guarded by `fetched` — callers control when to call this:
  //   - the tab-switch effect fires once (guarded by `fetched`)
  //   - Retry button calls it directly
  const fetchPreview = useCallback(async () => {
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const data = await getResultJson(sessionId);
      setResultData(data);
      setFetched(true);
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : "Failed to load data");
    } finally {
      setPreviewLoading(false);
    }
  }, [sessionId, getResultJson]);

  useEffect(() => {
    if (activeTab === "preview" && !fetched && !previewError && !previewLoading) {
      fetchPreview();
    }
  }, [activeTab, fetched, previewError, previewLoading, fetchPreview]);

  const tabs: { key: Tab; label: string }[] = [
    { key: "summary", label: "Summary" },
    { key: "preview", label: "Data Preview" },
    { key: "downloads", label: "Downloads" },
  ];

  return (
    <div style={styles.container}>
      {/* Tab bar */}
      <div style={styles.tabBar}>
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            style={activeTab === tab.key ? styles.tabActive : styles.tabInactive}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={styles.content}>
        {activeTab === "summary" && (
          <SummaryTab complete={complete} runStartTime={runStartTime} />
        )}
        {activeTab === "preview" && (
          <DataPreviewTab
            data={resultData}
            loading={previewLoading}
            error={previewError}
            onRetry={fetchPreview}
          />
        )}
        {activeTab === "downloads" && (
          <DownloadsTab sessionId={sessionId} />
        )}
      </div>
    </div>
  );
}

// --- Summary Tab ---

function SummaryTab({ complete, runStartTime }: { complete: CompleteData; runStartTime: number | null }) {
  const cards = [
    { label: "Total Tokens", value: complete.total_tokens.toLocaleString() },
    { label: "Est. Cost", value: `$${complete.cost.toFixed(4)}` },
    { label: "Elapsed", value: formatElapsed(runStartTime) },
  ];

  return (
    <div>
      <div style={styles.cardGrid}>
        {cards.map((c) => (
          <div key={c.label} style={styles.card}>
            <div style={styles.cardLabel}>{c.label}</div>
            <div style={styles.cardValue}>{c.value}</div>
          </div>
        ))}
        <div style={styles.card}>
          <div style={styles.cardLabel}>Status</div>
          <div>
            {complete.success ? (
              <span style={styles.successBadge}>✓ Success</span>
            ) : (
              <span style={styles.failBadge}>✗ Failed</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// --- Data Preview Tab ---

function DataPreviewTab({
  data,
  loading,
  error,
  onRetry,
}: {
  data: Record<string, unknown> | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (loading) {
    return <div style={styles.loading}>Loading data preview...</div>;
  }

  if (error) {
    return (
      <div style={styles.fetchError}>
        {error}
        <br />
        <button onClick={onRetry} style={styles.retryButton}>Retry</button>
      </div>
    );
  }

  if (!data) {
    return <div style={styles.loading}>No data available</div>;
  }

  // Extract fields from result.json
  const fields = (data.fields || data) as Record<string, unknown>;
  const entries = Object.entries(fields);

  return (
    <table style={styles.table}>
      <thead>
        <tr>
          <th style={styles.th}>Field Name</th>
          <th style={styles.th}>Value</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([name, value], i) => {
          const isEmpty = value === null || value === undefined || value === "";
          return (
            <tr key={name} style={{ background: i % 2 === 0 ? pwc.white : pwc.grey50 }}>
              <td style={isEmpty ? styles.tdEmpty : styles.td}>{name}</td>
              <td style={isEmpty ? styles.tdEmpty : styles.td}>
                {isEmpty ? "—" : String(value)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// --- Downloads Tab ---

function DownloadsTab({ sessionId }: { sessionId: string }) {
  const downloads = [
    { label: "Download Excel", filename: "filled.xlsx", icon: "📊" },
    { label: "Download JSON", filename: "result.json", icon: "📄" },
    { label: "Download Trace", filename: "conversation_trace.json", icon: "🔍" },
  ];

  return (
    <div style={styles.downloadRow}>
      {downloads.map((d) => (
        <a
          key={d.filename}
          href={`/api/result/${sessionId}/${d.filename}`}
          download={d.filename}
          style={styles.downloadButton}
        >
          <span>{d.icon}</span>
          {d.label}
        </a>
      ))}
    </div>
  );
}
