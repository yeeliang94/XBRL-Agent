import { useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { PageHeader } from "../components/PageHeader";
import { getSettings, updateSettings, testConnection } from "../lib/api";
import { GeneralSettingsForm } from "../components/GeneralSettingsForm";
import { AccountTab } from "../components/AccountTab";
import { UsersTab } from "../components/UsersTab";

// ---------------------------------------------------------------------------
// SettingsPage — the consolidated settings surface that replaces the gear's
// settings modal. Three tabs (gotcha #7: inline styles; WAI-ARIA tabs pattern
// mirroring RunDetailView):
//   General  — model / proxy / API key + run defaults (the old modal body)
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
}

type TabKey = "general" | "account" | "users";

export function SettingsPage({ isAdmin, currentEmail }: Props) {
  const tabs: { key: TabKey; label: string }[] = [
    { key: "general", label: "General" },
    { key: "account", label: "Account" },
    ...(isAdmin ? [{ key: "users" as const, label: "Users" }] : []),
  ];

  const [activeTab, setActiveTab] = useState<TabKey>("general");

  const tabBarRef = useRef<HTMLDivElement>(null);
  const onTabKeyDown = (e: React.KeyboardEvent, index: number) => {
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    else return;
    e.preventDefault();
    setActiveTab(tabs[next].key);
    const btns = tabBarRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    btns?.[next]?.focus();
  };

  return (
    <div className="responsive-page settings-page" style={styles.container}>
      <PageHeader
        title="Settings"
        description="Model and proxy configuration, run defaults, your account, and user management."
      />

      <div
        ref={tabBarRef}
        style={styles.tabBar}
        role="tablist"
        aria-label="Settings sections"
      >
        {tabs.map((t, i) => {
          const active = t.key === activeTab;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={active}
              tabIndex={active ? 0 : -1}
              className="pwc-tab"
              onClick={() => setActiveTab(t.key)}
              onKeyDown={(e) => onTabKeyDown(e, i)}
              style={active ? styles.tabActive : styles.tab}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {activeTab === "general" && (
        <section style={styles.section} role="tabpanel">
          <GeneralSettingsForm
            getSettings={getSettings}
            saveSettings={updateSettings}
            testConnection={testConnection}
            isAdmin={isAdmin}
          />
        </section>
      )}

      {activeTab === "account" && (
        <section style={styles.section} role="tabpanel">
          <AccountTab />
        </section>
      )}

      {activeTab === "users" && isAdmin && (
        <section style={styles.section} role="tabpanel">
          <UsersTab currentEmail={currentEmail} />
        </section>
      )}
    </div>
  );
}

const styles = {
  // Form mode (design-system Layouts): 840px, matching the other pages'
  // task-based widths instead of a bespoke 640 cap.
  container: {
    ...ui.pageForm,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as React.CSSProperties,
  tabBar: {
    ...ui.tabBar,
  } as React.CSSProperties,
  // Shared underline tab geometry; active = dark text + orange indicator.
  tab: ui.tab,
  tabActive: {
    ...ui.tab,
    ...ui.tabActive,
  } as React.CSSProperties,
  section: {} as React.CSSProperties,
};
