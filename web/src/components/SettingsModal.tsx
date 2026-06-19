import { useEffect } from "react";
import type { SettingsResponse } from "../lib/types";
import { pwc } from "../lib/theme";
import { GeneralSettingsForm } from "./GeneralSettingsForm";

// ---------------------------------------------------------------------------
// SettingsModal — thin overlay wrapper around GeneralSettingsForm.
//
// The form body was extracted into GeneralSettingsForm so the consolidated
// Settings page can host it as a tab without an overlay. This wrapper keeps the
// modal presentation (used by the legacy gear-opens-a-dialog path and pinned by
// SettingsModal.test.tsx): the overlay, Escape-to-close, and click-outside-to-
// close all live here; everything else lives in the form.
// ---------------------------------------------------------------------------

interface Props {
  isOpen: boolean;
  onClose: () => void;
  getSettings: () => Promise<SettingsResponse & { auto_review?: boolean; entity_memory?: boolean }>;
  saveSettings: (body: Partial<{ api_key: string; model: string; proxy_url: string; auto_review: boolean; entity_memory: boolean }>) => Promise<{ status: string }>;
  testConnection: (body: Partial<{ proxy_url: string; api_key: string; model: string }>) => Promise<{ status: string; model?: string; latency_ms?: number; message?: string }>;
}

const styles = {
  overlay: {
    position: "fixed" as const,
    inset: 0,
    zIndex: 50,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(0,0,0,0.4)",
  } as React.CSSProperties,
  modal: {
    background: pwc.white,
    borderRadius: pwc.radius.lg,
    boxShadow: pwc.shadow.modal,
    width: "100%",
    maxWidth: 480,
    padding: pwc.space.xl,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: pwc.weight.medium,
    fontSize: 18,
    color: pwc.grey900,
    margin: 0,
    marginBottom: pwc.space.xl,
  } as React.CSSProperties,
};

export function SettingsModal({ isOpen, onClose, getSettings, saveSettings, testConnection }: Props) {
  // Escape closes the dialog (only while open).
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      style={styles.overlay}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
    >
      <div style={styles.modal}>
        <h2 style={styles.heading}>Settings</h2>
        {/* `key={String(isOpen)}` remounts the form each time the modal opens so
            it re-loads the latest settings (the form loads once on mount). */}
        <GeneralSettingsForm
          key={String(isOpen)}
          getSettings={getSettings}
          saveSettings={saveSettings}
          testConnection={testConnection}
          onCancel={onClose}
        />
      </div>
    </div>
  );
}
