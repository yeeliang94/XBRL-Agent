import { useId } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ui } from "../lib/uiStyles";
import { ds } from "../lib/theme";

const styles = {
  field: {
    display: "flex",
    flexDirection: "column",
    gap: ds.space.sm,
    minWidth: 0,
  } as CSSProperties,
  group: {
    ...ui.segmentGroup,
    display: "flex",
    alignSelf: "stretch",
  } as CSSProperties,
  option: {
    ...ui.segmentButton,
    flex: "1 1 auto",
    padding: `0 ${ds.space.sm}px`,
    whiteSpace: "nowrap",
  } as CSSProperties,
  optionDisabled: {
    cursor: "not-allowed",
  } as CSSProperties,
  optionActiveDisabled: {
    color: ds.grey700,
  } as CSSProperties,
};

/** One labelled segmented choice that fills its filing-setup grid column. */
export function SetupChoice<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled = false,
  children,
}: {
  label: string;
  options: readonly { value: T; label: string; title?: string }[];
  value: T;
  onChange: (next: T) => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  const labelId = useId();
  return (
    <div style={styles.field}>
      <span id={labelId} style={ui.fieldLabel}>{label}</span>
      <div className="segmented-control-group" role="group" aria-labelledby={labelId} style={styles.group}>
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              className="segmented-control-button"
              aria-pressed={active}
              title={option.title}
              disabled={disabled}
              onClick={() => onChange(option.value)}
              style={{
                ...styles.option,
                ...(active ? ui.segmentButtonActive : {}),
                ...(disabled ? styles.optionDisabled : {}),
                ...(disabled && active ? styles.optionActiveDisabled : {}),
              }}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      {children}
    </div>
  );
}
