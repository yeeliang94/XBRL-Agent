import { useEffect, useRef } from "react";
import { pwc } from "../lib/theme";

interface ResizableDividerProps {
  testId: string;
  label: string;
  onDelta: (dx: number) => void;
}

/**
 * One consistent pane divider: a one-pixel visible rule with a wider,
 * transparent drag target. The larger hit area keeps resizing usable without
 * turning the boundary into a heavy bar.
 */
export function ResizableDivider({
  testId,
  label,
  onDelta,
}: ResizableDividerProps) {
  const cleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => () => cleanupRef.current?.(), []);

  const onMouseDown = (event: React.MouseEvent) => {
    event.preventDefault();
    let lastX = event.clientX;

    const move = (moveEvent: MouseEvent) => {
      const dx = moveEvent.clientX - lastX;
      lastX = moveEvent.clientX;
      onDelta(dx);
    };
    const teardown = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.userSelect = "";
      cleanupRef.current = null;
    };
    const up = () => {
      teardown();
    };

    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    document.body.style.userSelect = "none";
    cleanupRef.current = teardown;
  };

  return (
    <div
      className="review-resize-handle"
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      tabIndex={0}
      data-testid={testId}
      onMouseDown={onMouseDown}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        onDelta(event.key === "ArrowLeft" ? -16 : 16);
      }}
      style={styles.hitArea}
    >
      <span
        aria-hidden="true"
        style={styles.rule}
      />
    </div>
  );
}

const styles = {
  hitArea: {
    flex: "0 0 9px",
    alignSelf: "stretch",
    minHeight: 240,
    display: "flex",
    justifyContent: "center",
    cursor: "col-resize",
    background: "transparent",
    position: "relative" as const,
    zIndex: 1,
  } as React.CSSProperties,
  rule: {
    display: "block",
    width: 1,
    minHeight: "100%",
    background: pwc.grey100,
    transition: "background 120ms ease",
  } as React.CSSProperties,
} as const;
