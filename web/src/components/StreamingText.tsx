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
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    fontSize: 13,
    fontFamily: pwc.fontBody,
    margin: `${pwc.space.sm}px 0`,
  } as React.CSSProperties,
  th: {
    background: pwc.grey100,
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    fontSize: 12,
    color: pwc.grey900,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    textAlign: "left" as const,
    borderBottom: `2px solid ${pwc.grey200}`,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  td: {
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey800,
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

// Detect if a line is a markdown table row (starts and ends with |)
function isTableRow(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.length > 2;
}

// Detect separator row (| --- | --- |)
function isSeparatorRow(line: string): boolean {
  return isTableRow(line) && /^\|[\s:*-]+(\|[\s:*-]+)+\|$/.test(line.trim());
}

// Parse cells from a table row
function parseCells(line: string): string[] {
  const trimmed = line.trim();
  // Remove leading/trailing pipes and split by |
  return trimmed.slice(1, -1).split("|").map((c) => c.trim());
}

// Render a markdown table from consecutive table lines
function renderTable(lines: string[], key: number): React.ReactNode {
  // Find header, separator, and body rows
  const headerLine = lines[0];
  const hasHeader = lines.length >= 2 && isSeparatorRow(lines[1]);
  const headerCells = parseCells(headerLine);
  const bodyLines = hasHeader ? lines.slice(2) : lines.slice(1);

  return (
    <div key={key} style={{ overflowX: "auto", margin: `${pwc.space.sm}px 0` }}>
      <table style={styles.table}>
        {hasHeader && (
          <thead>
            <tr>
              {headerCells.map((cell, ci) => (
                <th key={ci} style={styles.th}>{renderInline(cell)}</th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {(hasHeader ? bodyLines : lines).map((row, ri) => {
            const cells = parseCells(row);
            return (
              <tr key={ri} style={{ background: ri % 2 === 0 ? pwc.white : pwc.grey50 }}>
                {cells.map((cell, ci) => (
                  <td key={ci} style={styles.td}>{renderInline(cell)}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
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

// Group lines into blocks: consecutive table rows become a single table,
// all other lines render individually.
function renderBlocks(lines: string[]): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let i = 0;
  let blockKey = 0;

  while (i < lines.length) {
    // Only enter table mode when the next line is a separator row (| --- | --- |),
    // which is the standard markdown table format: header → separator → body.
    if (
      isTableRow(lines[i]) &&
      i + 1 < lines.length &&
      isSeparatorRow(lines[i + 1])
    ) {
      // Collect consecutive table rows (header + separator + body)
      const tableLines: string[] = [];
      while (i < lines.length && isTableRow(lines[i])) {
        tableLines.push(lines[i]);
        i++;
      }
      nodes.push(renderTable(tableLines, blockKey++));
    } else if (isTableRow(lines[i])) {
      // Pipe-delimited line without a following separator — render as normal text
      nodes.push(renderLine(lines[i], blockKey++));
      i++;
    } else {
      nodes.push(renderLine(lines[i], blockKey++));
      i++;
    }
  }

  return nodes;
}

export function StreamingText({ text, isStreaming }: Props) {
  if (!text) return null;

  const lines = text.split("\n");
  return (
    <div style={{ ...styles.container, whiteSpace: "normal" }}>
      {renderBlocks(lines)}
      {isStreaming && <span data-testid="streaming-caret" style={styles.caret} />}
    </div>
  );
}
