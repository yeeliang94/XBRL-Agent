# PLAN — Motion polish (transitions.dev, folded in)

**Status:** IMPLEMENTED (2026-07-11, branch `feat/motion-transitions`). All
phases landed; full web suite green (1042 tests).

**What shipped:**
- Foundation: `AnimatedNumber` (+ count-up hook) and `TabPanelFade` components,
  and the `slide-in-right` keyframe. First-mount shows the final value instantly
  (no roll-from-zero); reduced-motion snaps.
- Count-up: home StatTiles (rows 1) and the live TokenDashboard counters +
  cost (row 3) — the surfaces where numbers actually change in-session.
- Tab crossfade: RunDetailView panels via one `TabPanelFade` keyed on the
  active tab (row 5).
- Cross-checks: staggered row fade-up (row 7) + status-pill crossfade on
  live flip (row 11).
- Live/list entrances: agent activity cards (row 8), History rows (row 9),
  NeedsAttentionPanel reveal (row 12), toast slide-in (row 10).

**Deliberate deviations from the §3 proposal** (both toward "keep it minimum"):
- The count-up tween is **co-located inside `AnimatedNumber.tsx`**, not extracted
  as a `useCountUp` hook in `numberFormat.ts` — there is only one consumer, so a
  separate hook would be a speculative abstraction; `numberFormat.ts` stays pure.
- The JS tween uses an ease-out-cubic function (documented analogue of the
  `pwc.motion.easing` cubic-bezier) because a CSS easing string can't drive a
  JS tween; only `pwc.motion.duration.slow` is read literally.
- NeedsAttentionPanel reveal is **fade-only**, not "fade + height" — a height
  animation on a variable-height panel is finicky (the `slide-down` keyframe
  caps at 500px) and reads as fussy; fade alone stays calm.

**Open-question defaults taken** (§9): (1) numeric notes cells — NOT animated
(no motion on editable financial figures; lowest value, highest distraction
risk); (2) toast — entrance only, dismiss stays instant (keeps the ref-stable
timer untouched). Deliberately skipped as low-value / static-only: eval score
count-up (static once loaded), copy-icon swap, NotesSubTabBar content fade (its
chip bar already transitions). Revisit if the operator wants them.

---
_Original proposal below._

**Decisions taken with the operator:**
- **Ambition — "fill gaps, stay calm."** Add motion only where it is genuinely
  missing, all within the existing restraint budget. The product should feel
  more finished, never flashy.
- **Approach — fold into existing tokens.** Hand-pick the relevant
  transitions.dev CSS, adapt each to `web/src/lib/theme.ts` motion tokens +
  `web/src/index.css`, keep ONE motion system. No new runtime dependency, no
  `transitions-refine` tool, no agent skill install.
- **Priority — all surfaces**, sequenced into phases (review surfaces first,
  then live-run, then global polish), but every surface below is in scope.

---

## 1. What this is, in plain language

transitions.dev is a catalogue of small, copy-paste CSS animations (a number
that rolls when it changes, a panel that fades as you switch tabs, a
notification that slides in). We are **not** installing it as a library — we
are borrowing a handful of its ideas and rewriting them in the vocabulary this
app already speaks, so the whole UI still feels like one designed system rather
than a pile of imported effects.

The goal is polish, not novelty: make the moments where the screen *changes*
(a run finishes, a number updates, you switch tabs, checks stream in) feel
smooth and intentional instead of snapping.

## 2. The system we already have (and must not fight)

This codebase is not a blank canvas. It already has a deliberate motion layer:

- **`web/src/lib/theme.ts` → `pwc.motion`** is the single budget: three
  durations (`fast 150ms`, `base 200ms`, `slow 250ms`) and **one** easing curve
  (`cubic-bezier(0.2, 0, 0, 1)` — decelerate, no overshoot). Everything must
  read from these. No new durations, no bouncy/spring curves.
- **`web/src/index.css`** holds the shared `@keyframes` (`fade-in`,
  `slide-down`, `dialog-in`, `skeleton-shimmer`, `glyph-pulse`, `pulse`, `spin`)
  and the global **`prefers-reduced-motion`** block that zeroes every
  animation/transition. Any new motion inherits this off-switch for free.
- **Existing motion already lives on:** pipeline step pulse, disclosure chevron
  rotate + slide-down, skeleton shimmer, modal entrance, card hover-lift, button
  hovers, agent-tab hover.

**Hard guardrails (from `CLAUDE.md`):**
- Gotcha #7 — inline styles only, no Tailwind; `theme.ts` is the single cascade
  point; anything `:hover`/keyframe-based lives in `index.css`.
- Gotcha #7 — many frontend tests assert **exact RGB** from `theme.ts` tokens,
  and tab queries must stay scoped by `aria-label`. Motion changes must not
  perturb colour assertions or the tablist role structure.
- The "done" bar is a **passing pinning test**, not "looks right."

Practical consequence: almost all of this work is **additive CSS + a couple of
tiny reusable primitives**. There is no schema change, no backend change, no
new dependency.

## 3. Foundation (build once, reuse everywhere)

Three small pieces unlock most of the surfaces below.

### 3.1 `AnimatedNumber` component + `useCountUp` hook
- New: `web/src/components/AnimatedNumber.tsx` (+ hook in
  `web/src/lib/numberFormat.ts`, which already owns `formatAccounting` /
  `formatGroupedInput` / `formatCost`).
- Counts from the previous value to the new one over `pwc.motion.duration.slow`
  using the shared easing, formatting each frame through the existing
  formatter so grouping/parentheses stay consistent.
- **Reduced-motion:** snaps straight to the final value (respects the global
  media query — the hook checks `matchMedia("(prefers-reduced-motion: reduce)")`
  and skips the tween).
- Guardrails: only animate when the value actually *changes* on screen (not on
  first mount of a static historical run — see §7). Integer vs. 2-dp is a prop.

### 3.2 `TabPanelFade` wrapper
- New: `web/src/components/TabPanelFade.tsx` — a thin wrapper that re-runs the
  existing `fade-in` keyframe (opacity + 4px rise) whenever its `key` changes.
- Applied in `RunDetailView.tsx` around each `role="tabpanel"` section
  (the `{activeTab === "…" && <section role="tabpanel">}` blocks, ~L792–927),
  keyed on `activeTab`. Also reusable for `NotesSubTabBar` panels.
- **Must not** change the tablist/tab role structure or `aria-label`s (keeps
  gotcha #7 tab-scoping tests green).

### 3.3 (Optional) one new keyframe: `slide-in-right`
- For the toast entrance/exit (§6.C). transform/opacity only, `base` duration.
- Everything else reuses keyframes that already exist.

## 4. Surface catalogue (everything in scope)

Each row: where it lives · what changes · transitions.dev idea borrowed ·
effort (S/M/L) · risk.

| # | Surface | File(s) | Motion added | Idea | Effort | Risk |
|---|---|---|---|---|---|---|
| 1 | Headline stat tiles (218 runs, drafts, completed) | `StatTiles.tsx` | count-up on value change | number animation | S | low |
| 2 | Eval score % + cross-check "6/7" counters | `EvalTab.tsx`, `RunDetailView` overview tiles | count-up / roll | number animation | S | low |
| 3 | Token & cost figures in telemetry | `TokenDashboard.tsx`, `AgentTelemetryPanel.tsx` | count-up (settled values) | number animation | S | low |
| 4 | Numeric notes cells (sheets 13/14) | `NotesReviewTab` NumericCellRow | gentle value-change flash, no reflow | number animation | S | med |
| 5 | Run-detail tab switch | `RunDetailView.tsx` | panel crossfade/rise on tab change | content transition | M | med |
| 6 | Notes sub-tab switch (Sheet-12) | `NotesSubTabBar.tsx` | same crossfade | content transition | S | med |
| 7 | Cross-check rows streaming in live | `ValidatorTab.tsx` | staggered fade-up as each result lands | list item entrance | M | med |
| 8 | Agent activity cards streaming in | `AgentTabs.tsx`/`AgentTimeline.tsx` | fade-up on append | list item entrance | M | med |
| 9 | History rows on load | `HistoryList.tsx` | subtle fade-in of the batch | list entrance | S | low |
| 10 | Success/error toast | `SuccessToast.tsx` | slide-in on appear, fade-out on dismiss | notification | S | low |
| 11 | Status pill flips (pass→fail, run status chip) | `runStatus.ts` consumers, cross-check pills | crossfade the colour/label change | badge/state | S | med |
| 12 | Pipeline "needs attention" / alert banners | `NeedsAttentionPanel.tsx` | fade+height reveal on appear | modal/reveal | S | low |
| 13 | Copy-to-clipboard buttons (notes) | `clipboard` consumers, toolbar | icon swap ✓ on success | icon swap | S | low |
| 14 | Cards that resize (expanding review/needs-attention) | `Disclosure` already; extend to review cards | height/opacity ease | card resize | M | med |

**Already good — leave alone:** modal entrance (`dialog-in`), disclosure
chevron+slide, skeleton shimmer, card hover-lift, button hovers, pipeline pulse.
No change; they set the taste the rest should match.

## 5. Phasing (all surfaces, sequenced)

- **Phase 0 — Foundation.** §3.1 `AnimatedNumber`/`useCountUp`, §3.2
  `TabPanelFade`, §3.3 keyframe. Unit-tested in isolation (incl. reduced-motion
  snaps-to-value). No visible change yet.
- **Phase 1 — Review surfaces (highest daily payoff).** Rows 1, 2, 5, 6, 7, 11.
  This is where the operator + reviewers spend post-run time.
- **Phase 2 — Live-run / streaming.** Rows 7 (streaming variant), 8, 12, and the
  Extract-page pipeline feel. Verified against a real streaming run.
- **Phase 3 — Global polish.** Rows 3, 4, 9, 10, 13, 14 — consistency pass so
  toasts, lists, numbers, and icon swaps behave the same everywhere.

Each phase is independently shippable and independently revertible (all motion
is additive CSS + opt-in wrappers).

## 6. Notes on the trickier three

**A. Tab crossfade (row 5).** Wrapping panels in `TabPanelFade` re-triggers on
`activeTab`. Risk: lazy-mounted heavy tabs (Values/Notes editor) shouldn't
fade *while* loading — fade the container, let the skeleton handle the load.
Keep the fade to opacity+rise so it never causes layout shift.

**B. Live streaming lists (rows 7, 8).** Only newly-appended rows animate; the
existing list must not re-animate on every SSE tick. Implement by animating on
mount of a new keyed row only (CSS `animation` on first paint), not on parent
re-render.

**C. Toast (row 10).** Add a mount slide-in and a dismiss fade-out. The
component currently unmounts instantly on dismiss; a clean exit needs a short
"leaving" state before unmount (≤ `base`). Keep the existing 4s auto-dismiss
and the ref-stable timer untouched.

## 7. Testing & verification (the "done" bar)

- **Don't break colour/role pins.** Run the existing `StatTiles`,
  `NotesReviewTab`, `RunDetailView`, `ValidatorTab`, `HistoryList` web tests —
  motion is additive, but confirm no RGB/tab-scope assertion moved.
- **New pinning tests:**
  - `AnimatedNumber` renders the final value immediately under
    `prefers-reduced-motion` (mock `matchMedia`), and reaches it after tween
    otherwise.
  - `TabPanelFade` re-keys on tab change without altering panel content/roles.
  - Toast mounts with the entrance animation name and survives auto-dismiss.
- **Live check (browser preview):** drive one real streaming run on
  `http://localhost:8002` and confirm rows 7/8/10 animate on *new* content only,
  no reflow, and a `prefers-reduced-motion` emulation shows instant states
  (`resize_window` colorScheme + DevTools reduced-motion, or verify the global
  media block zeroes them).
- **Never** animate a static historical run's numbers on first mount (would
  read as gratuitous). Count-up fires only on an in-session value *change*.

## 8. Explicit non-goals

- No new dependency; no `framer-motion`; no `transitions-refine` CLI; no agent
  skill install.
- No new easing curves or durations beyond `pwc.motion`.
- No motion on dense financial tables at rest (the figure grids), no
  attention-grabbing pulses on error states beyond what exists.
- No backend / SSE / schema changes.

## 9. Open questions for the operator

1. Numeric notes cells (row 4): is a value-change flash welcome, or is any
   motion on the editable figures distracting while typing? (Default: skip while
   focused, subtle flash only on external change.)
2. Toast exit animation (row 10) requires a brief "leaving" state — acceptable,
   or keep instant-dismiss and only animate entrance?
