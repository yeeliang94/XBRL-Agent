import { pwc } from "../lib/theme";

interface Props {
  text: string;
  isStreaming: boolean;
}

const styles = {
  container: {
    fontFamily: pwc.fontBody,
    fontSize: 15,
    color: pwc.grey800,
    lineHeight: 1.6,
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  } as React.CSSProperties,
  caret: {
    display: "inline-block",
    width: 2,
    height: "1em",
    background: pwc.orange500,
    marginLeft: 1,
    verticalAlign: "text-bottom",
    animation: "blink-caret 0.8s step-end infinite",
  } as React.CSSProperties,
  code: {
    fontFamily: pwc.fontMono,
    fontSize: "0.9em",
    background: pwc.grey100,
    padding: "1px 5px",
    borderRadius: 3,
    border: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  bullet: {
    color: pwc.orange500,
    fontWeight: 600,
    marginRight: 4,
  } as React.CSSProperties,
};

// Tokenize inline markdown: **bold**, *italic*, `code`.
// Safe (no HTML injection) — all output is plain React nodes.
function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // One regex with alternation + lookaround to capture all three.
  const re = /(\*\*([^*\n]+)\*\*)|(`([^`\n]+)`)|(\*([^*\n]+)\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    if (match[1] !== undefined) {
      nodes.push(<strong key={`b${key++}`}>{match[2]}</strong>);
    } else if (match[3] !== undefined) {
      nodes.push(
        <code key={`c${key++}`} style={styles.code}>
          {match[4]}
        </code>,
      );
    } else if (match[5] !== undefined) {
      nodes.push(<em key={`i${key++}`}>{match[6]}</em>);
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

// Render a single line, handling leading "- " bullets and "### " headings.
function renderLine(line: string, key: number): React.ReactNode {
  const bulletMatch = /^(\s*)[-*]\s+(.*)$/.exec(line);
  if (bulletMatch) {
    const [, indent, rest] = bulletMatch;
    return (
      <div key={key}>
        {indent}
        <span style={styles.bullet}>•</span>
        {renderInline(rest)}
      </div>
    );
  }
  const headingMatch = /^(#{1,6})\s+(.*)$/.exec(line);
  if (headingMatch) {
    const level = headingMatch[1].length;
    const size = Math.max(15, 22 - level * 2);
    return (
      <div key={key} style={{ fontSize: size, fontWeight: 700, marginTop: 8, marginBottom: 4 }}>
        {renderInline(headingMatch[2])}
      </div>
    );
  }
  if (line.length === 0) return <div key={key}>&nbsp;</div>;
  return <div key={key}>{renderInline(line)}</div>;
}

export function StreamingText({ text, isStreaming }: Props) {
  if (!text) return null;

  const lines = text.split("\n");
  return (
    <div style={{ ...styles.container, whiteSpace: "normal" }}>
      {lines.map((line, i) => renderLine(line, i))}
      {isStreaming && <span data-testid="streaming-caret" style={styles.caret} />}
    </div>
  );
}
