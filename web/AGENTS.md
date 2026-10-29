# web/ — Agent Rules

These rules add to the root `AGENTS.md`. Detail: invariants 7, 16, 19, 21, 27,
28, and 30 in `CLAUDE-REFERENCE.md`.

- Keep component styling in inline `style={}` props. Shared tokens live in
  `web/src/lib/theme.ts`, shared layout primitives in
  `web/src/lib/uiStyles.ts`, and interaction or responsive states in
  `web/src/index.css`.
- For any layout, navigation, content, style, or accessibility change, read
  `docs/xbrl-design-system.html` before editing; it is the
  canonical UI/UX authority. Direction A in `docs/prototype-ui-overhaul.html` supplies
  composition examples only; the design guide wins when they differ.
- Apply the design guide's clarity-first contract across the full affected
  workflow: remove repeated explanation and internal metadata, reserve
  indicators for exceptions, keep actions contextual, align shared page
  boundaries, contain long content and tables, preserve source-PDF context, and
  surface every material waiting stage. Update shared rules, implementation,
  and pinning tests together.
- Scope ARIA-tab tests to the labelled tab list and keep heavy tab content
  lazy-mounted.
- Notes HTML in the editor follows the `data-source-styled="true"` contract in
  `notes/AGENTS.md`; keep the TipTap schema and review CSS synchronized with it.
