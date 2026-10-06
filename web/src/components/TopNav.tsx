import type { AppView } from "../lib/appReducer";
import { ui } from "../lib/uiStyles";

export function TopNav({ view, onViewChange }: { view: AppView; onViewChange: (view: AppView) => void }) {
  return <nav aria-label="Main navigation">
    <a href="/" aria-current={view !== "settings" && view !== "concepts" ? "page" : undefined}
      style={{ ...ui.buttonQuiet, ...ui.tabActive, textDecoration: "none" }}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onViewChange("extract");
      }}>Documents</a>
  </nav>;
}
