import { useEffect, useState } from "react";

/** Reveal rapid token bursts on animation frames while retaining all text. */
export function useFrameBatchedText(content: string, isActive: boolean): string {
  const [displayed, setDisplayed] = useState(() => (isActive ? "" : content));

  useEffect(() => {
    if (!isActive) {
      setDisplayed(content);
      return;
    }

    if (typeof window === "undefined" || typeof window.requestAnimationFrame !== "function") {
      setDisplayed(content);
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      setDisplayed((current) => {
        if (!content.startsWith(current)) return content;
        const remaining = content.length - current.length;
        if (remaining <= 0) return current;
        const step = Math.max(1, Math.ceil(remaining * 0.55));
        return content.slice(0, current.length + step);
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [content, displayed, isActive]);

  // Replacing a streamed update can swap the whole string between renders.
  // Show that replacement immediately so stale text never flashes.
  return content.startsWith(displayed) ? displayed : content;
}
