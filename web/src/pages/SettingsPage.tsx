import { useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { PageHeader } from "../components/PageHeader";
import { getSettings, updateSettings, testConnection } from "../lib/api";
import { type SharedSettingsSection, GeneralSettingsForm } from "../components/GeneralSettingsForm";
import { AccountTab } from "../components/AccountTab";
import { UsersTab } from "../components/UsersTab";
import { AgentInstructionsPanel } from "../components/AgentInstructionsPanel";
import { NotesAppearanceSettings } from "../components/NotesAppearanceSettings";

// ---------------------------------------------------------------------------
// SettingsPage — the consolidated settings surface that replaces the gear's
// settings modal. Settings tabs (gotcha #7: inline styles; WAI-ARIA tabs pattern
// mirroring RunDetailView):
//   General  — model / proxy / API key + run defaults (the old modal body)
//   Agent instructions — shared content guidance and read-only prompt sources
//   Account  — change my own password
//   Users    — admin-only user management (hidden unless isAdmin)
// Tab content is mounted lazily (only the active panel renders) so the Users
// tab's list fetch and the General tab's settings load don't fire until shown.
// ---------------------------------------------------------------------------

interface Props {
  // The Users tab is admin-only. The page also relies on the server enforcing
  // it, but hiding the tab keeps non-admins from seeing a 403 surface.
  isAdmin: boolean;
  // Signed-in admin's email — used by UsersTab to hide self-destructive
  // actions on the admin's own row (UX-QA #13).
  currentEmail?: string;
  onFieldLabels?: () => void;
}

type TabKey = SharedSettingsSection | "instructions" | "notes" | "account" | "users";

export function SettingsPage({ isAdmin, currentEmail, onFieldLabels }: Props) {
  const tabs: { key: TabKey; label: string }[] = [
    { key: "general", label: "General" },
    { key: "instructions", label: "Agent instructions" },
    { key: "extraction", label: "Extraction" },
    { key: "advanced", label: "Advanced" },
    { key: "notes", label: "Notes appearance" },
    { key: "account", label: "Account" },
    ...(isAdmin ? [{ key: "users" as const, label: "Users" }] : []),
  ];

  const [activeTab, setActiveTab] = useState<TabKey>("general");
  const [instructionsDirty, setInstructionsDirty] = useState(false);
  const [sharedDirty, setSharedDirty] = useState(false);
  const [sharedBusy, setSharedBusy] = useState(false);
  const sharedTab = (key: TabKey): key is SharedSettingsSection => ["general", "extraction", "advanced"].includes(key);
  const [notesBusy, setNotesBusy] = useState(false);
  const changeTab = (key: TabKey) => {
    if (key === activeTab) return true;
    if (notesBusy || sharedBusy) return false;
    if (sharedDirty && sharedTab(activeTab) && !sharedTab(key)) {
      if (!window.confirm("Discard unsaved settings?")) return false;
      setSharedDirty(false);
    }
    if (instructionsDirty && !window.confirm("Discard unsaved guidance?")) return false;
    setInstructionsDirty(false);
    setActiveTab(key);
    return true;
  };

  const tabBarRef = useRef<HTMLDivElement>(null);
  const onTabKeyDown = (e: React.KeyboardEvent, index: number) => {
    let next = index;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (index + 1) % tabs.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (index - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    else return;
    e.preventDefault();
    if (!changeTab(tabs[next].key)) return;
    const btns = tabBarRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    btns?.[next]?.focus();
  };

  return (
    <div className="responsive-page settings-page" style={styles.container}>
      <PageHeader title="Settings" />
      {isAdmin && onFieldLabels && <button type="button" style={{ ...ui.buttonGhost, alignSelf: "flex-start" }} onClick={onFieldLabels}>Field labels</button>}

      <div className="settings-section-layout" style={styles.layout}>
      <div
        ref={tabBarRef}
        style={styles.tabBar}
        role="tablist"
        aria-label="Settings sections"
        aria-orientation="vertical"
      >
        {tabs.map((t, i) => {
          const active = t.key === activeTab;
          return (
            <button
              key={t.key}
              type="button"
              id={`settings-tab-${t.key}`}
              aria-controls={`settings-panel-${t.key}`}
              role="tab"
              aria-selected={active}
              disabled={(notesBusy || sharedBusy) && !active}
              tabIndex={active ? 0 : -1}
              className="pwc-tab"
              onPointerDown={(e) => e.currentTarget.setAttribute("data-pointer-focus", "true")}
              onBlur={(e) => e.currentTarget.removeAttribute("data-pointer-focus")}
              onClick={() => changeTab(t.key)}
              onKeyDown={(e) => {
                e.currentTarget.removeAttribute("data-pointer-focus");
                onTabKeyDown(e, i);
              }}
              style={active ? styles.tabActive : styles.tab}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div style={{ minWidth: 0 }}>
      {sharedTab(activeTab) && (
        <section className="pwc-view-enter" style={styles.section} id={`settings-panel-${activeTab}`} aria-labelledby={`settings-tab-${activeTab}`} role="tabpanel">
          <GeneralSettingsForm
            getSettings={getSettings}
            saveSettings={updateSettings}
            testConnection={testConnection}
            isAdmin={isAdmin}
            section={activeTab}
            onDirtyChange={setSharedDirty}
            onBusyChange={setSharedBusy}
          />
        </section>
      )}

      {activeTab === "notes" && (
        <section style={styles.section} id="settings-panel-notes" aria-labelledby="settings-tab-notes" role="tabpanel">
          <NotesAppearanceSettings onBusyChange={setNotesBusy} />
        </section>
      )}

      {activeTab === "instructions" && (
        <section style={styles.section} id="settings-panel-instructions" aria-labelledby="settings-tab-instructions" role="tabpanel">
          <AgentInstructionsPanel isAdmin={isAdmin} onDirtyChange={setInstructionsDirty} />
        </section>
      )}

      {activeTab === "account" && (
        <section className="pwc-view-enter" style={styles.section} id="settings-panel-account" aria-labelledby="settings-tab-account" role="tabpanel">
          <AccountTab />
        </section>
      )}

      {activeTab === "users" && isAdmin && (
        <section className="pwc-view-enter" style={styles.section} id="settings-panel-users" aria-labelledby="settings-tab-users" role="tabpanel">
          <UsersTab currentEmail={currentEmail} />
        </section>
      )}
      </div>
      </div>
    </div>
  );
}

const styles = {
  // Standard page width leaves room for the section rail and aligned controls.
  container: {
    ...ui.pageStandard,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as React.CSSProperties,
  layout: { display: "grid", gridTemplateColumns: "190px minmax(0, 1fr)", gap: 32, alignItems: "start" } as React.CSSProperties,
  tabBar: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
    minWidth: 0,
  } as React.CSSProperties,
  // Shared surface-tab geometry; active = dark text + quiet fill.
  tab: { ...ui.tab, textAlign: "left", justifyContent: "flex-start", width: "100%" } as React.CSSProperties,
  tabActive: {
    ...ui.tab,
    ...ui.tabActive,
    textAlign: "left",
    justifyContent: "flex-start",
    width: "100%",
  } as React.CSSProperties,
  section: {} as React.CSSProperties,
};
