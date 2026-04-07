import { pwc } from "../lib/theme";

interface Props {
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  onAutoDetect: () => void;
  isDetecting: boolean;
  canAutoDetect: boolean;  // false until a PDF is uploaded
}

const styles = {
  container: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 14,
    color: pwc.grey900,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  // Custom toggle switch (checkbox is visually hidden, label is the switch)
  toggle: {
    position: "relative" as const,
    width: 36,
    height: 20,
    borderRadius: 10,
    cursor: "pointer",
    transition: "background 0.2s",
  } as React.CSSProperties,
  toggleKnob: {
    position: "absolute" as const,
    top: 2,
    width: 16,
    height: 16,
    borderRadius: "50%",
    background: pwc.white,
    transition: "left 0.2s",
    boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
  } as React.CSSProperties,
  detectButton: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 500,
    color: pwc.grey900,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
  } as React.CSSProperties,
  detectButtonDisabled: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 500,
    color: pwc.grey300,
    background: pwc.grey50,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    cursor: "not-allowed",
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
  } as React.CSSProperties,
  spinner: {
    width: 12,
    height: 12,
    border: `2px solid ${pwc.grey200}`,
    borderTop: `2px solid ${pwc.orange500}`,
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
    display: "inline-block",
  } as React.CSSProperties,
};

export function ScoutToggle({
  enabled,
  onToggle,
  onAutoDetect,
  isDetecting,
  canAutoDetect,
}: Props) {
  return (
    <div style={styles.container}>
      <label style={styles.label}>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onToggle(e.target.checked)}
          style={{
            position: "absolute" as const,
            opacity: 0,
            width: 0,
            height: 0,
          }}
        />
        {/* Visual toggle track */}
        <span
          style={{
            ...styles.toggle,
            background: enabled ? pwc.orange500 : pwc.grey300,
          }}
        >
          <span
            style={{
              ...styles.toggleKnob,
              left: enabled ? 18 : 2,
            }}
          />
        </span>
        Scout
      </label>

      {enabled && (
        <button
          onClick={onAutoDetect}
          disabled={!canAutoDetect || isDetecting}
          style={
            !canAutoDetect || isDetecting
              ? styles.detectButtonDisabled
              : styles.detectButton
          }
        >
          {isDetecting ? (
            <>
              <span style={styles.spinner} />
              Detecting...
            </>
          ) : (
            "Auto-detect"
          )}
        </button>
      )}
    </div>
  );
}
