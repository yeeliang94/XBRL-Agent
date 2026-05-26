// PwC design tokens — the single cascade point for the whole web UI.
// ~30 components import this `pwc` object and build inline styles from it
// (CLAUDE.md gotcha #7 bans a utility framework like Tailwind), so changing a
// value here re-themes the app. Values are anchored to the committed
// reference at docs/pwc-design-system.html. Token NAMES are kept stable so
// the cascade doesn't break; new tokens are added rather than renamed.
//
// NOTE: several frontend tests assert the exact rgb() form of these hexes.
// Change a value and update its pinning test in the same commit.
export const pwc = {
  // Primary
  black: '#000000',
  white: '#FFFFFF',
  orange500: '#FD5108',   // Core orange (Pantone 1655C) — primary accent, active states, links
  orange700: '#E0470A',   // Darkened orange — hover / pressed states
  orange400: '#FE7C39',   // Light accent — progress bars, active indicators
  orange300: '#FFAA72',   // Tint
  orange200: '#FFCDA8',   // Tint
  orange100: '#FFE8D4',   // Tint — backgrounds for highlighted content
  orange50:  '#FFF5ED',   // Subtle tint — hover backgrounds, empty field highlight, focus ring

  // Greys — pure-neutral scale (was blue-tinted; now matches the design
  // system's neutral ladder). Mapped by visual role so layouts don't shift.
  grey50:  '#FAFAFA',     // Page background (design n-25)
  grey100: '#F4F4F4',     // Card backgrounds, alternating rows, table headers (n-50)
  grey200: '#DEDEDE',     // Borders, dividers (n-200)
  grey300: '#C9C9C9',     // Strong borders, disabled text, pending connectors (n-300)
  grey500: '#7D7D7D',     // Secondary / muted text, timestamps (n-500)
  grey700: '#5E5E5E',     // Tertiary text, field labels (n-600)
  grey800: '#2D2D2D',     // Body text (n-800)
  grey900: '#1A1A1A',     // Headings, primary text (n-900)

  // Semantic
  success:      '#059669',  // Green — completed steps, success badges
  error:        '#DC2626',  // Red — errors, failed states
  info:         '#2F6FB0',  // Blue — informational states
  thinking:     '#7C3AED',  // Purple — agent thinking/reasoning blocks (functional, not brand)

  // Semantic surfaces (backgrounds/borders/text tints for status UI).
  // Centralized so the status look-and-feel can be themed in one place
  // instead of replicating literals across components. Soft fills come from
  // the design system; text shades are kept deliberately darker than the
  // base status hue for legible contrast on those soft fills.
  successBg:    '#E6F4EF',  // Tinted background for pass/success panels + badges
  successBorder:'#C8E6D2',  // Border colour that pairs with successBg
  successText:  '#166534',  // Foreground text on successBg (darker than `success` for contrast)
  errorBg:      '#FBE9E9',  // Tinted background for fail/error panels + badges
  errorBorder:  '#F4CFCA',  // Border colour that pairs with errorBg
  errorText:    '#991B1B',  // Strong error text (headings, badge labels)
  errorTextAlt: '#B91C1C',  // Secondary error text (body copy, tracebacks)
  infoBg:       '#ECF3FA',  // Tinted background for info panels + badges
  infoBorder:   '#CFE0F0',  // Border colour that pairs with infoBg

  // Warning surfaces — partial-success / non-fatal diagnostics. Used by the
  // notes pipeline to surface writer skips, borderline fuzzy matches, and
  // partial sub-agent coverage without flipping a run to "failed". Amber
  // soft-fill from the design system; text kept dark for legibility.
  warningBg:     '#FDF4E0',
  warningBorder: '#F4E2B0',
  warningText:   '#92400E',

  // Typography — single Helvetica Neue family across the system (no licensed
  // PwC corporate face available). Hierarchy comes from size + weight, with
  // large headings sitting at light weight per the design principles.
  fontHeading: '"Helvetica Neue", Helvetica, Arial, system-ui, sans-serif',
  fontBody:    '"Helvetica Neue", Helvetica, Arial, system-ui, sans-serif',
  fontMono:    '"SF Mono", ui-monospace, Menlo, Consolas, monospace',

  // Weight scale — avoid 700+ in product UI.
  weight: { light: 300, regular: 400, medium: 500, semibold: 600 },

  // Spacing scale (px) — 4px base. xxxl/xxxxl added to match the design
  // system's larger section rhythm (s-7 / s-8).
  space: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48, xxxxl: 64 },

  // Border radius — pill added for badges/toggles.
  radius: { sm: 3, md: 6, lg: 10, pill: 999 },

  // Shadows — soft, low-contrast, diffuse (design system's three levels).
  shadow: {
    // Resting elevation for cards. Two faint layers read cleaner than a
    // single hard drop-shadow when cards nest (the review workspace stacks
    // several).
    card: '0 1px 2px rgba(26,26,25,0.04), 0 1px 3px rgba(26,26,25,0.06)',
    elevated: '0 2px 4px rgba(26,26,25,0.04), 0 4px 12px rgba(26,26,25,0.07)',
    modal: '0 8px 28px rgba(26,26,25,0.10)',
  },
} as const;
