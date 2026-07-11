import { pwc } from "../lib/theme";

// ---------------------------------------------------------------------------
// TabPanelFade — re-runs the shared `fade-in` keyframe (opacity + 4px rise)
// whenever `tabKey` changes, so switching a tab crossfades the new panel in
// instead of hard-swapping. Keyed on tabKey: React remounts the wrapper on
// every change, which restarts the CSS animation without any JS.
//
// Opacity + tiny transform only — never layout-shifting — and it inherits the
// global prefers-reduced-motion off-switch in index.css for free. Adds one
// plain <div>; it does NOT carry the tabpanel role (the child <section>
// keeps role="tabpanel"), so the tab-scoping tests (gotcha #7) are unaffected.
// ---------------------------------------------------------------------------

interface Props {
  /** Change this to re-trigger the fade (e.g. the active tab key). */
  tabKey: string;
  children: React.ReactNode;
}

export function TabPanelFade({ tabKey, children }: Props) {
  return (
    <div
      key={tabKey}
      style={{ animation: `fade-in ${pwc.motion.duration.base} ${pwc.motion.easing}` }}
    >
      {children}
    </div>
  );
}
