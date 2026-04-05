import { useState } from "react";
import type { ThinkingBlock as ThinkingBlockType } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  /** Finalized block (null while still streaming) */
  block: ThinkingBlockType | null;
  isStreaming: boolean;
  streamingContent: string;
}

const styles = {
  wrapper: {
    borderLeft: `3px solid ${pwc.thinking}`,
    background: pwc.grey50,
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    animation: "fade-in 0.2s ease-out",
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    color: pwc.thinking,
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  pulsingDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: pwc.thinking,
    animation: "pulse-subtle 1.5s ease-in-out infinite",
    display: "inline-block",
  } as React.CSSProperties,
  content: {
    fontFamily: pwc.fontMono,
    fontSize: 13,
    color: pwc.grey800,
    lineHeight: 1.5,
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  } as React.CSSProperties,
  collapsedRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    cursor: "pointer",
    border: "none",
    background: "none",
    width: "100%",
    textAlign: "left" as const,
    padding: 0,
  } as React.CSSProperties,
  summary: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    flex: 1,
  } as React.CSSProperties,
  duration: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey500,
    flexShrink: 0,
    marginLeft: pwc.space.sm,
  } as React.CSSProperties,
  expandedContent: {
    fontFamily: pwc.fontMono,
    fontSize: 13,
    color: pwc.grey800,
    lineHeight: 1.5,
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
    maxHeight: 200,
    overflowY: "auto" as const,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
};

export function ThinkingBlock({ block, isStreaming, streamingContent }: Props) {
  const [expanded, setExpanded] = useState(false);

  // Streaming mode — block not yet finalized
  if (isStreaming && !block) {
    return (
      <div data-testid="thinking-wrapper" style={styles.wrapper}>
        <div style={styles.label}>
          <span style={styles.pulsingDot} />
          Thinking...
        </div>
        <div data-testid="thinking-content" style={styles.content}>
          {streamingContent}
        </div>
      </div>
    );
  }

  // Finalized block — collapsed/expandable
  if (!block) return null;

  // Fixed reasoning duration from the server (ms). If unavailable, hide the label
  // rather than showing misleading "time since end" info.
  const durationLabel =
    block.durationMs != null
      ? block.durationMs >= 1000
        ? `Thought for ${(block.durationMs / 1000).toFixed(1)}s`
        : `Thought for ${block.durationMs}ms`
      : "";

  return (
    <div data-testid="thinking-wrapper" style={styles.wrapper}>
      <button
        onClick={() => setExpanded(!expanded)}
        style={styles.collapsedRow}
        role="button"
      >
        <span style={styles.summary}>{block.summary}</span>
        {durationLabel && <span style={styles.duration}>{durationLabel}</span>}
      </button>
      {expanded && (
        <div style={styles.expandedContent}>{block.content}</div>
      )}
    </div>
  );
}
