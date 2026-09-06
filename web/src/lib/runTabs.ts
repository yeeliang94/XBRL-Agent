export type RunTabKey =
  | "overview"
  | "agents"
  | "notes"
  | "checks"
  | "telemetry"
  | "review"
  | "values"
  | "eval";

export const RUN_TAB_KEYS: readonly RunTabKey[] = [
  "overview",
  "agents",
  "notes",
  "checks",
  "telemetry",
  "review",
  "values",
  "eval",
];

export const RUN_TAB_CHANGE_EVENT = "xbrl-run-tab-change";

export function readRunTabFromUrl(): RunTabKey | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("tab");
  return raw && (RUN_TAB_KEYS as readonly string[]).includes(raw)
    ? (raw as RunTabKey)
    : /^\/concepts\/\d+\/?$/.test(window.location.pathname) ? "values" : null;
}

export function announceRunTabChange(key: RunTabKey): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<RunTabKey>(RUN_TAB_CHANGE_EVENT, { detail: key }));
}

export function writeRunTabToUrl(key: RunTabKey): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.set("tab", key);
  // Each visited section is a browser-history entry; repeated clicks are not.
  if (url.toString() !== window.location.href) {
    window.history.pushState(window.history.state, "", url.toString());
  }
  announceRunTabChange(key);
}
