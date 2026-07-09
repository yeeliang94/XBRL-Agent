import { useRef, useState } from "react";
import { pwc } from "../lib/theme";
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
    <div style={styles.container}>
      <h2 style={styles.heading}>Settings</h2>

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

const tabBase: React.CSSProperties = {
  padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
  fontFamily: pwc.fontHeading,
  fontSize: 15,
  fontWeight: pwc.weight.medium,
  background: "none",
  border: "none",
  borderBottom: "2px solid transparent",
  cursor: "pointer",
  outline: "none",
  color: pwc.grey500,
};

const styles = {
  container: {
    maxWidth: 640,
    margin: "0 auto",
    padding: pwc.space.xl,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: pwc.weight.medium,
    fontSize: 22,
    color: pwc.grey900,
    margin: 0,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  tabBar: {
    display: "flex",
    gap: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey200}`,
    marginBottom: pwc.space.xl,
  } as React.CSSProperties,
  tab: tabBase,
  tabActive: {
    ...tabBase,
    color: pwc.orange500,
    borderBottom: `2px solid ${pwc.orange500}`,
  } as React.CSSProperties,
  section: {} as React.CSSProperties,
};
