// Copy server-prepared notes as rich HTML and readable plain text.
// Formatting and mTool transport markup are owned by the backend exporter.

/** Encode a string as a `text/plain` clipboard variant. This is a
 *  best-effort plaintext rendering — the editor also keeps the exact
 *  HTML in the text/html variant for rich targets.
 *
 *  `textContent` alone drops paragraph breaks, list markers, and
 *  table row separators so plain-text paste targets (Google Sheets,
 *  Numbers, Notes) see mashed-together content. Walk the DOM tree
 *  and emit structural whitespace so the plaintext form is still
 *  readable: `\n\n` between block paragraphs, `- ` / `1. ` prefixes
 *  for list items, and ` | `-separated row/cell text for tables.
 *  Like `notes/html_to_text.py`, it preserves structure; inline whitespace
 *  collapses here for browser paste targets while the backend keeps it. */
export function htmlToPlaintext(html: string): string {
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  const out: string[] = [];
  renderNodeToPlaintext(tmp, out, { listStack: [] });
  // Collapse 3+ blank lines back to 2 so repeated `<p>` or nested
  // blocks don't leave gaps the user has to clean up on paste.
  return out.join("").replace(/\n{3,}/g, "\n\n").trim();
}

/** Context threaded through the recursive walker — only tracks the
 *  current list stack so nested `<ol>` numbering doesn't double-count
 *  on a `<ul>` inside an `<ol>`. */
interface PlaintextCtx {
  // Each stack frame = one open list. `type` is "ul" / "ol";
  // `index` is the next item number to emit for ordered lists.
  listStack: Array<{ type: "ul" | "ol"; index: number }>;
}

function renderNodeToPlaintext(
  node: Node,
  out: string[],
  ctx: PlaintextCtx,
): void {
  if (node.nodeType === Node.TEXT_NODE) {
    // Collapse runs of whitespace inside inline text the way an
    // HTML renderer would — preserves single spaces between words
    // while dropping author-side formatting indents.
    const text = (node.textContent ?? "").replace(/\s+/g, " ");
    if (text) out.push(text);
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;

  const el = node as Element;
  const tag = el.tagName.toLowerCase();

  switch (tag) {
    case "br":
      out.push("\n");
      return;
    case "p":
    case "div":
    case "h1":
    case "h2":
    case "h3":
    case "h4":
    case "h5":
    case "h6": {
      walkChildren(el, out, ctx);
      out.push("\n\n");
      return;
    }
    case "ul":
    case "ol": {
      ctx.listStack.push({ type: tag, index: 1 });
      walkChildren(el, out, ctx);
      ctx.listStack.pop();
      // Trailing separator so a list followed by a paragraph has a
      // gap. Collapsed back to `\n\n` in the caller's final pass.
      out.push("\n");
      return;
    }
    case "li": {
      const frame = ctx.listStack[ctx.listStack.length - 1];
      const prefix = frame?.type === "ol"
        ? `${frame.index++}. `
        : "- ";
      out.push(prefix);
      walkChildren(el, out, ctx);
      out.push("\n");
      return;
    }
    case "table": {
      walkChildren(el, out, ctx);
      out.push("\n");
      return;
    }
    case "thead":
    case "tbody":
    case "tfoot": {
      walkChildren(el, out, ctx);
      return;
    }
    case "tr": {
      // Render the cells ourselves so we can join with ` | ` and
      // avoid the default inline-concatenation textContent gives us.
      const cells: string[] = [];
      for (const child of Array.from(el.children)) {
        const childTag = child.tagName.toLowerCase();
        if (childTag === "td" || childTag === "th") {
          const sub: string[] = [];
          walkChildren(child, sub, ctx);
          cells.push(sub.join("").trim());
        }
      }
      out.push(cells.join(" | "));
      out.push("\n");
      return;
    }
    // Inline tags — no structural whitespace, just descend into
    // children so their text content bubbles up untouched.
    default:
      walkChildren(el, out, ctx);
      return;
  }
}

function walkChildren(node: Node, out: string[], ctx: PlaintextCtx): void {
  for (const child of Array.from(node.childNodes)) {
    renderNodeToPlaintext(child, out, ctx);
  }
}

/** Copy server-prepared output verbatim; never decorate transport HTML twice. */
export async function copyPreparedHtmlAsRichText(html: string, plainSource = html): Promise<boolean> {
  // Prefer the async Clipboard API. It needs BOTH `navigator.clipboard`
  // and the `ClipboardItem` global — we feature-detect both.
  const hasClipboardItem =
    typeof (globalThis as { ClipboardItem?: unknown }).ClipboardItem !==
    "undefined";
  const nav = globalThis.navigator as Navigator | undefined;
  if (nav?.clipboard?.write && hasClipboardItem) {
    try {
      const plain = htmlToPlaintext(plainSource);
      const ClipboardItemCtor = (
        globalThis as unknown as {
          ClipboardItem: new (items: Record<string, Blob>) => unknown;
        }
      ).ClipboardItem;
      const item = new ClipboardItemCtor({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([plain], { type: "text/plain" }),
      });
      await nav.clipboard.write([item as unknown as ClipboardItem]);
      return true;
    } catch {
      // Fall through to the legacy path — permission prompts in some
      // browsers reject the write() even when the API is present.
    }
  }

  // Legacy fallback: render the HTML into a hidden contenteditable div
  // (so execCommand('copy') sees a Selection over live DOM, which is
  // what lets the browser preserve the `text/html` format), select it,
  // fire the command, and clean up. Returns false if even that fails.
  try {
    const holder = document.createElement("div");
    holder.contentEditable = "true";
    // Preserve the server-prepared markup in the fallback too.
    holder.innerHTML = html;
    // Pull off-screen rather than display:none — hidden elements cannot
    // carry a Selection in most browsers.
    holder.style.position = "fixed";
    holder.style.left = "-9999px";
    holder.style.top = "0";
    holder.style.opacity = "0";
    document.body.appendChild(holder);
    try {
      const range = document.createRange();
      range.selectNodeContents(holder);
      const sel = window.getSelection();
      if (!sel) return false;
      sel.removeAllRanges();
      sel.addRange(range);
      const ok = document.execCommand("copy");
      sel.removeAllRanges();
      return ok;
    } finally {
      document.body.removeChild(holder);
    }
  } catch {
    return false;
  }
}
