// XBRL focused-workspace design tokens — the single cascade point for the
// whole web UI. `pwc` remains the object name only for source compatibility
// with existing imports; docs/xbrl-design-system.html and Direction A in
// docs/prototype-ui-overhaul.html are the visual source of truth.
//
// NOTE: several frontend tests assert the exact rgb() form of these hexes.
// Change a value and update its pinning test in the same commit.
export const pwc = {
  // Primary — the focused-workspace visual language is deliberately narrow:
  // black and white carry hierarchy, while orange is reserved for identity,
  // current activity, and attention.
  black: '#000000',
  white: '#FFFFFF',
  orange500: '#FD5108',   // Core orange (Pantone 1655C) — primary accent, active states, links
  orange700: '#D64000',   // Accessible orange for small text and interactive labels
  orange400: '#FE7C39',   // Light accent — progress bars, active indicators
  orange300: '#FFAA72',   // Tint
  orange200: '#FFCDA8',   // Tint
  orange100: '#FFE8D4',   // Tint — backgrounds for highlighted content
  orange50:  '#FFF5ED',   // Subtle tint — hover backgrounds, empty field highlight, focus ring

  // Greys — the cool-neutral ladder defined by the XBRL focused-workspace
  // specification. Existing token names stay stable for component consumers.
  grey50:  '#F5F7F8',
  grey100: '#EEEFF1',
  grey200: '#DFE3E6',
  grey300: '#CBD1D6',
  grey400: '#B5BCC4',
  grey500: '#6B7280',     // Readable muted text and essential control boundaries
  grey700: 'rgba(0, 0, 0, 0.64)', // compatibility alias: secondary text
  grey800: '#000000',              // compatibility alias: primary text
  grey900: '#000000',

  // Semantic — bright, clean status family pitched to sit on the light theme
  // without going dark or muddy (design-system Color section). Base hues drive
  // dots, icons and left-rules; the *Text tokens below carry status LABELS.
  success:      '#000000',  // Finished — black, always paired with text/icon
  warning:      '#FD5108',  // Attention — orange, always paired with text/icon
  error:        '#FD5108',  // Blocking/failed — orange plus explicit error copy
  info:         '#000000',  // Information stays monochrome
  thinking:     '#6B7280', // @deprecated compatibility alias

  // Status text — darker shade of each hue, AA-legible on neutral/light
  // surfaces. Carries status LABELS and inline coloured text (card deltas,
  // do/don't headers); the bright base hue is for dots/icons/borders.
  successText:  '#000000',
  errorText:    '#000000',
  errorTextAlt: 'rgba(0, 0, 0, 0.64)',
  warningText:  '#000000',
  infoText:     '#000000',

  // Soft tints — RESERVED for rare emphasis (e.g. highlighting one
  // reconciliation row), NOT the default surface for badges/alerts (those are
  // now outline/left-rule on a neutral surface). Light, airy, near-neutral
  // versions of the hues above; centralized so the look can be themed in one
  // place instead of replicating literals across components.
  successBg:    '#F5F7F8',
  successBorder:'#CBD1D6',
  errorBg:      '#FFF5ED',
  errorBorder:  '#FFCDA8',
  infoBg:       '#F5F7F8',
  infoBorder:   '#DFE3E6',
  warningBg:     '#FFF5ED',
  warningBorder: '#FFCDA8',

  // Typography — Inter where available, then the native UI stack. Hierarchy
  // stays intentionally compact: page, section, body.
  fontHeading: '"Inter Variable", Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontBody:    '"Inter Variable", Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontMono:    '"SFMono-Regular", Consolas, "Liberation Mono", monospace',

  // Weight scale. Product UI uses TWO text weights — regular (body/data) +
  // semibold (titles/headings/emphasis/labels); medium only on interactive
  // controls. `light` (300) is retained for token-name stability but is NOT
  // used in product UI (it reads as a different typeface); never 700+.
  weight: { light: 300, regular: 400, medium: 650, semibold: 680, bold: 700 },

  // Spacing scale (px) — 4px base. xxxl/xxxxl added to match the design
  // system's larger section rhythm (s-7 / s-8).
  space: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48, xxxxl: 64 },

  // Border radius. Role guidance (design-system Spacing & radius): sm for
  // dense cells / compact references · md for buttons, inputs, alerts ·
  // lg for cards and panels · xl only for large feature surfaces · pill for
  // toggles and exceptional compact tags.
  radius: { sm: 6, md: 8, lg: 12, xl: 12, pill: 999 },

  // Resting surfaces are flat. Only UI that overlaps content may float.
  shadow: {
    card: 'none',
    elevated: '0 14px 40px rgba(0, 0, 0, 0.12)',
    modal: '0 14px 40px rgba(0, 0, 0, 0.12)',
  },

  // Motion — short, purposeful transitions that clarify causality. Components
  // animate transform/opacity only; progress state itself always updates
  // immediately. Reduced-motion parity is enforced globally in index.css.
  motion: {
    duration: { instant: '100ms', fast: '140ms', base: '180ms', slow: '240ms' },
    easing: 'cubic-bezier(0.2, 0, 0, 1)',
    easingEmphasized: 'cubic-bezier(0.16, 1, 0.3, 1)',
  },
} as const;

// ---------------------------------------------------------------------------
// Semantic token layer (design-system "Semantic token architecture").
//
// Three layers: global values (the raw `pwc` object above) → semantic roles
// (this `tokens` object) → component roles (`component` below). Page
// components consume MEANING (`tokens.color.text.secondary`), not palette
// names, so the visual identity can evolve without a search-and-replace.
//
// Selection rule: choose a token because its name matches the purpose, never
// because its current value happens to look right.
// ---------------------------------------------------------------------------
// Accessible action values (plan: app-wide design consistency). Semantic and
// independently changeable from signature orange.
const ACTION_PRIMARY = '#000000';
const ACTION_PRIMARY_HOVER = '#000000';

export const tokens = {
  color: {
    // Signature orange is IDENTITY — marks, progress and compact attention
    // icons. Interactive selection uses surfaces, never accent edge lines.
    brand: {
      accent: pwc.orange500,
      indicator: pwc.orange500,
    },
    // Accessible interaction roles. Deliberately darker than the signature
    // orange and independently changeable from it (plan: app-wide design
    // consistency). Contrast with white: primary 5.16:1 · hover 6.42:1.
    action: {
      primary: ACTION_PRIMARY,
      primaryHover: ACTION_PRIMARY_HOVER,
      quietHover: pwc.grey100,
    },
    text: {
      primary: pwc.black,
      body: pwc.black,
      secondary: 'rgba(0, 0, 0, 0.64)',
      muted: 'rgba(0, 0, 0, 0.46)',
      onAction: pwc.white,      // text on a filled primary action
    },
    border: {
      subtle: pwc.grey200,      // hairline dividers, card borders
      strong: pwc.grey300,      // decorative emphasis borders, disabled
      control: pwc.grey500,
    },
    focus: {
      ring: pwc.black,
      halo: pwc.grey100,
      strong: pwc.black,
    },
  },
  surface: {
    canvas: pwc.white,
    default: pwc.white,
    sunken: pwc.grey50,
    navigation: pwc.grey50,
  },
  space: {
    inset: pwc.space.md,        // dense inner padding
    group: pwc.space.lg,        // between related controls
    section: pwc.space.xxl,     // between page sections
  },
  radius: {
    cell: pwc.radius.sm,        // dense cells, compact references
    control: pwc.radius.md,     // buttons, inputs, alerts
    panel: pwc.radius.lg,       // cards, panels, bordered groups
    feature: pwc.radius.xl,     // large feature surfaces only
    pill: pwc.radius.pill,
  },
  // Canonical task-based page widths (design-system Layouts & density). The
  // app shell owns the route-level mode; pages must not invent another cap.
  layout: {
    auth: 380,                  // Login
    form: 840,                  // Settings, focused configuration forms
    standard: 1500,
    wideList: 1500,
    // Workspace mode (run report, Figures, PDF review) is full available width.
  },
} as const;

// Component token layer — stable per-component decisions consumed by the
// shared primitives in uiStyles.ts and the state hooks in index.css.
export const component = {
  button: {
    primary: {
      background: tokens.color.action.primary,
      backgroundHover: tokens.color.action.primaryHover,
      text: tokens.color.text.onAction,
    },
    quiet: {
      backgroundHover: tokens.color.action.quietHover,
    },
  },
  table: {
    header: {
      surface: tokens.surface.sunken,
      text: tokens.color.text.secondary,
    },
  },
  dialog: {
    scrim: 'rgba(26, 26, 26, 0.45)',
  },
  nav: {
    activeText: tokens.color.text.primary,
    hoverSurface: tokens.surface.canvas,
  },
} as const;
