// Number app-owned entries so a cancelled Back/Forward can return to the
// original entry without replacing a destination or deleting forward history.
const INDEX = "xbrlHistoryIndex";
let currentIndex = 0;
let restoring = false;
let confirmLeave: (() => boolean) | null = null;

function indexCurrentEntry(): void {
  const index = window.history.state?.[INDEX];
  if (typeof index === "number") {
    currentIndex = index;
  } else {
    // Native fragment links create entries without carrying history.state.
    currentIndex += 1;
    window.history.replaceState({ ...window.history.state, [INDEX]: currentIndex }, "");
  }
}

export function initializeNavigationHistory(): () => void {
  currentIndex = window.history.state?.[INDEX] ?? 0;
  window.history.replaceState({ ...window.history.state, [INDEX]: currentIndex }, "");
  const onPop = (event: PopStateEvent) => {
    const nextIndex = event.state?.[INDEX];
    if (restoring) {
      restoring = false;
      event.stopImmediatePropagation();
      return;
    }
    if (typeof nextIndex === "number" && nextIndex !== currentIndex && confirmLeave && !confirmLeave()) {
      event.stopImmediatePropagation();
      restoring = true;
      window.history.go(currentIndex - nextIndex);
      return;
    }
    if (typeof nextIndex === "number") currentIndex = nextIndex;
  };
  const onHashChange = () => { if (!restoring) indexCurrentEntry(); };
  window.addEventListener("popstate", onPop, true);
  window.addEventListener("hashchange", onHashChange);
  return () => {
    window.removeEventListener("popstate", onPop, true);
    window.removeEventListener("hashchange", onHashChange);
    confirmLeave = null;
    restoring = false;
  };
}

export function pushNavigationHistory(data: unknown, unused: string, url?: string | URL | null): void {
  // Stamp even if the preceding native hashchange event has not arrived yet.
  indexCurrentEntry();
  const nextIndex = currentIndex + 1;
  window.history.pushState({ ...(data as object), [INDEX]: nextIndex }, unused, url);
  currentIndex = nextIndex;
}

export function guardNavigationHistory(confirm: () => boolean): () => void {
  confirmLeave = confirm;
  return () => { if (confirmLeave === confirm) confirmLeave = null; };
}

export function confirmNavigationLeave(): boolean {
  return confirmLeave?.() ?? true;
}
