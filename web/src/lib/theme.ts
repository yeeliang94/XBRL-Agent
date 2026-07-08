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

  // Semantic — bright, clean status family pitched to sit on the light theme
  // without going dark or muddy (design-system Color section). Base hues drive
  // dots, icons and left-rules; the *Text tokens below carry status LABELS.
  success:      '#1FAB76',  // Fresh green — completed steps, success dot/border
  warning:      '#EFA417',  // Warm gold — caution dot/border/left-rule
  error:        '#E5484D',  // Clean red — errors, failed states
  info:         '#3E84CC',  // Clean blue — informational states
  thinking:     '#8B5CF6',  // Violet — agent thinking/reasoning blocks (functional, not brand)

  // Status text — darker shade of each hue, AA-legible on neutral/light
  // surfaces. Carries status LABELS and inline coloured text (card deltas,
  // do/don't headers); the bright base hue is for dots/icons/borders.
  successText:  '#157A53',  // Foreground text for success status
  errorText:    '#C0303A',  // Strong error text (headings, badge labels)
  errorTextAlt: '#D14A4E',  // Secondary error text (body copy, tracebacks)
  warningText:  '#8A6111',  // Warning status text
  infoText:     '#2C6299',  // Info status text

  // Soft tints — RESERVED for rare emphasis (e.g. highlighting one
  // reconciliation row), NOT the default surface for badges/alerts (those are
  // now outline/left-rule on a neutral surface). Light, airy, near-neutral
  // versions of the hues above; centralized so the look can be themed in one
  // place instead of replicating literals across components.
  successBg:    '#E8F6EF',  // Tinted background for rare success emphasis
  successBorder:'#C8E9DA',  // Border colour that pairs with successBg
  errorBg:      '#FCECEC',  // Tinted background for rare error emphasis
  errorBorder:  '#F6D5D6',  // Border colour that pairs with errorBg
  infoBg:       '#EAF2FB',  // Tinted background for rare info emphasis
  infoBorder:   '#D2E2F3',  // Border colour that pairs with infoBg
  warningBg:     '#FCF3DF',
  warningBorder: '#F3E2BB',

  // Typography — single Helvetica Neue family across the system (no licensed
  // PwC corporate face available). Hierarchy comes from SIZE, not a wide weight
  // range: two text weights carry everything (design system Typography).
  fontHeading: '"Helvetica Neue", Helvetica, Arial, system-ui, sans-serif',
  fontBody:    '"Helvetica Neue", Helvetica, Arial, system-ui, sans-serif',
  fontMono:    '"SF Mono", ui-monospace, Menlo, Consolas, monospace',

  // Weight scale. Product UI uses TWO text weights — regular (body/data) +
  // semibold (titles/headings/emphasis/labels); medium only on interactive
  // controls. `light` (300) is retained for token-name stability but is NOT
  // used in product UI (it reads as a different typeface); never 700+.
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

  // Motion — one restraint budget for the whole UI ("depth felt, not seen").
  // Every transition/animation reads from these so nothing is bouncy or slow;
  // a single decelerate curve (no overshoot) and three short durations. A
  // global prefers-reduced-motion block in index.css zeroes these out.
  motion: {
    duration: { fast: '150ms', base: '200ms', slow: '250ms' },
    // Decelerate, no overshoot — enterprise-calm, never playful.
    easing: 'cubic-bezier(0.2, 0, 0, 1)',
  },
} as const;
